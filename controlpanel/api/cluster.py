import logging
import secrets

from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from github import Github, GithubException

from controlpanel.api.aws import aws, iam_arn
from controlpanel.api.helm import helm
from controlpanel.api.kubernetes import KubernetesClient


log = logging.getLogger(__name__)


BASE_ROLE_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "ec2.amazonaws.com",
            },
            "Action": "sts:AssumeRole",
        },
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": iam_arn(f"role/{settings.K8S_WORKER_ROLE_NAME}"),
            },
            "Action": "sts:AssumeRole",
        },
    ],
}

CONSOLE_STATEMENT = {
    "Sid": "console",
    "Action": [
        "s3:GetBucketLocation",
        "s3:ListAllMyBuckets",
    ],
    "Effect": "Allow",
    "Resource": ["arn:aws:s3:::*"],
}

OIDC_STATEMENT = {
    "Effect": "Allow",
    "Principal": {
        "Federated": iam_arn(f"oidc-provider/{settings.OIDC_DOMAIN}"),
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
}

READ_INLINE_POLICIES = f'{settings.ENV}-read-user-roles-inline-policies'

SAML_STATEMENT = {
    "Effect": "Allow",
    "Principal": {
        "Federated": iam_arn(f"saml-provider/{settings.SAML_PROVIDER}"),
    },
    "Action": "sts:AssumeRoleWithSAML",
    "Condition": {
        "StringEquals": {
            "SAML:aud": "https://signin.aws.amazon.com/saml",
        },
    },
}

TOOL_DEPLOYING = 'Deploying'
TOOL_DEPLOY_FAILED = 'Failed'
TOOL_IDLED = 'Idled'
TOOL_NOT_DEPLOYED = 'Not deployed'
TOOL_READY = 'Ready'
TOOL_UPGRADED = 'Upgraded'
TOOL_STATUS_UNKNOWN = 'Unknown'


def is_ignored_exception(e):
    ignored_exception_names = (
        'BucketAlreadyOwnedByYou',
        'EntityAlreadyExistsException',
        'NoSuchEntityException',
    )
    if e.__class__.__name__ in ignored_exception_names:
        log.error(f"Caught AWS exception and ignored: {e}")
        return True


def ignore_unwanted_exception(fn):
    def wraps(*args, **kwargs):
        try:
            return fn(*args, **kwargs)

        except Exception as e:
            if not is_ignored_exception(e):
                raise e
    return wraps


def initialize_user(user):
    role = IAMRole(user.iam_role_name)
    role.allow_assume_role_with_saml()
    role.allow_assume_role_with_oidc(sub=user.auth0_id)
    role.save()

    aws.attach_policy_to_role(
        role_name=user.iam_role_name,
        policy_arn=iam_arn(f"policy/{READ_INLINE_POLICIES}"),
    )

    helm.upgrade_release(
        f"init-user-{user.slug}",
        f"{settings.HELM_REPO}/init-user",
        f"--set=" + (
            f"Env={settings.ENV},"
            f"NFSHostname={settings.NFS_HOSTNAME},"
            f"OidcDomain={settings.OIDC_DOMAIN},"
            f"Email={user.email},"
            f"Fullname={user.name},"
            f"Username={user.slug}"
        ),
    )
    helm.upgrade_release(
        f"config-user-{user.slug}",
        f"{settings.HELM_REPO}/config-user",
        f"--namespace=user-{user.slug}",
        f"--set=Username={user.slug}",
    )


def purge_user(user):
    aws.delete_role(user.iam_role_name)
    helm.delete(helm.list_releases(f"--namespace=user-{user.slug}"))
    helm.delete(f"init-user-{user.slug}")


def create_app_role(name):
    role = IAMRole(name)
    role.save()


@ignore_unwanted_exception
def delete_app_role(name):
    aws.delete_role(name)


@ignore_unwanted_exception
def create_policy(policy_name, path="/"):
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [CONSOLE_STATEMENT]
    }
    aws.create_policy(policy_name, policy_document, path=path)


@ignore_unwanted_exception
def delete_policy(policy_arn):
    aws.delete_policy(policy_arn)


@ignore_unwanted_exception
def list_entities_for_policy(policy_arn, entity_filter="Role"):
    return aws.list_entities_for_policy(policy_arn, entity_filter)


@ignore_unwanted_exception
def attach_policy_to_role(policy_arn, role_name):
    aws.attach_policy_to_role(policy_arn, role_name)


@ignore_unwanted_exception
def detach_policy_from_role(policy_arn, role_name):
    aws.detach_policy_from_role(policy_arn, role_name)


class IAMRole:

    def __init__(self, role_name):
        self.policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "ec2.amazonaws.com",
                    },
                    "Action": "sts:AssumeRole",
                },
                {
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": iam_arn(f"role/{settings.K8S_WORKER_ROLE_NAME}"),
                    },
                    "Action": "sts:AssumeRole",
                },
            ],
        }
        self.role_name = role_name

    def save(self):
        try:
            aws.create_role(self.role_name, self.policy)
        except Exception as e:
            if not is_ignored_exception(e):
                raise e

    def allow_assume_role_with_saml(self):
        self.policy["Statement"].append(dict(SAML_STATEMENT))

    def allow_assume_role_with_oidc(self, sub):
        stmt = dict(OIDC_STATEMENT)
        stmt["Condition"] = {
            "StringEquals": {
                f"{settings.OIDC_DOMAIN}/:sub": sub,
            }
        }
        self.policy["Statement"].append(stmt)


class S3AccessPolicy(dict):
    POLICY_NAME = "s3-access"

    def __init__(self, role_name=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.role_name = role_name
        self._ro = set()
        self._rw = set()

        for statement in self.get("Statement", []):
            sid = statement.get("Sid")

            if sid == "readonly":
                self._ro = set([
                    arn.rstrip('/*')
                    for arn in statement["Resource"]
                ])

            elif sid == "readwrite":
                self._rw = set([
                    arn.rstrip('/*')
                    for arn in statement["Resource"]
                ])

    @classmethod
    def load(cls, role_name):
        document = aws.get_inline_policy_document(
            role_name=role_name,
            policy_name=cls.POLICY_NAME,
        )

        if document is None:
            return cls(role_name=role_name)

        policy = cls(role_name=role_name, **document)
        return policy

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.save()
        return True

    def save(self):
        aws.put_role_policy(
            self.role_name,
            policy_name=self.POLICY_NAME,
            policy_document=self,
        )

    def _update_statements(self):
        self["Statement"] = list(filter(None, [
            CONSOLE_STATEMENT,
            self.list_statement(self._ro | self._rw),
            self.readonly_statement(self._ro),
            self.readwrite_statement(self._rw),
        ]))

    def grant_access(self, bucket_arn, access_level="readonly"):
        if access_level in ("readonly", "readwrite"):
            if access_level == "readwrite":
                self._ro.discard(bucket_arn)
                self._rw.add(bucket_arn)
            elif access_level == "readonly":
                self._ro.add(bucket_arn)
                self._rw.discard(bucket_arn)
            self._update_statements()

    def revoke_access(self, bucket_arn):
        self._rw.discard(bucket_arn)
        self._ro.discard(bucket_arn)
        self._update_statements()

    def reset_access(self, bucket_arn):
        self._rw = {r for r in self._rw if not r.startswith(bucket_arn)}
        self._ro = {r for r in self._ro if not r.startswith(bucket_arn)}
        self._update_statements()

    def list_statement(self, resources):
        if resources:
            return {
                "Sid": "list",
                "Action": [
                    "s3:ListBucket",
                ],
                "Effect": "Allow",
                "Resource": list(resources),
            }

    def readonly_statement(self, resources):
        if resources:
            return {
                "Sid": "readonly",
                "Action": [
                    "s3:GetObject",
                    "s3:GetObjectAcl",
                    "s3:GetObjectVersion",
                ],
                "Effect": "Allow",
                "Resource": [
                    f"{arn.rstrip('/*')}/*"
                    for arn in resources
                ],
            }

    def readwrite_statement(self, resources):
        if resources:
            statement = dict(self.readonly_statement(resources))
            statement["Sid"] = "readwrite"
            statement["Action"].extend([
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:PutObject",
                "s3:PutObjectAcl",
                "s3:RestoreObject",
            ])
            return statement


class S3ManagedPolicy(S3AccessPolicy):
    def __init__(self, policy_arn, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy_arn = policy_arn

    @classmethod
    def load(cls, policy_arn):
        current_policy = aws.get_policy(
            policy_arn=policy_arn
        )

        if current_policy is None:
            return cls(policy_arn=policy_arn)

        document = aws.get_policy_version(
            policy_arn=policy_arn,
            version_id=current_policy["Policy"]["DefaultVersionId"],
        )["PolicyVersion"]["Document"]

        policy = cls(policy_arn=policy_arn, **document)
        return policy

    def save(self):
        aws.create_policy_version(
            self.policy_arn,
            policy_document=self,
        )

@ignore_unwanted_exception
def create_bucket(bucket_name, is_data_warehouse=False):
    result = aws.create_bucket(
        bucket_name,
        region=settings.BUCKET_REGION,
        acl='private',
    )
    aws.put_bucket_logging(
        bucket_name,
        target_bucket=settings.LOGS_BUCKET_NAME,
        target_prefix=f"{bucket_name}/",
    )
    aws.put_bucket_encryption(bucket_name)
    aws.put_public_access_block(bucket_name)
    if is_data_warehouse:
        aws.put_bucket_tagging(
            bucket_name,
            tags={'buckettype': 'datawarehouse'},
        )

    return result


@ignore_unwanted_exception
def create_parameter(name, value, role, description):
    return aws.create_parameter(name, value, role, description)


@ignore_unwanted_exception
def delete_parameter(name):
    aws.delete_parameter(name)


@ignore_unwanted_exception
def create_group(name, path="/"):
    return aws.create_group(name, path)


@ignore_unwanted_exception
def get_group(name):
    return aws.get_group(name)


@ignore_unwanted_exception
def add_user_to_group(group_name, user_name):
    return aws.add_user_to_group(group_name, user_name)


@ignore_unwanted_exception
def remove_user_from_group(group_name, user_name):
    return aws.remove_user_from_group(group_name, user_name)


@ignore_unwanted_exception
def delete_group(name):
    aws.delete_group(name)


def get_repositories(user):
    repos = []
    github = Github(user.github_api_token)
    for name in settings.GITHUB_ORGS:
        org = github.get_organization(name)
        repos.extend(org.get_repos())
    return repos


def get_repository(user, repo_name):
    github = Github(user.github_api_token)
    try:
        return github.get_repo(repo_name)
    except GithubException.UnknownObjectException:
        return None


class ToolDeploymentError(Exception):
    pass


def deploy_tool(tool, user, **kwargs):
    values = {
        'username': user.username.lower(),
        'Username': user.username.lower(),  # XXX backwards compatibility
        'aws.iamRole': user.iam_role_name,
        'toolsDomain': settings.TOOLS_DOMAIN,
    }

    # generate per-deployment secrets
    for key, value in tool.values.items():
        if value == '<SECRET_TOKEN>':
            tool.values[key] = secrets.token_hex(32)

    values.update(tool.values)
    values.update(kwargs)
    set_values = []
    for key, val in values.items():
        escaped_val = val.replace(',', '\,')
        set_values.extend(['--set', f'{key}={escaped_val}'])

    try:
        return helm.upgrade_release(
            f'{tool.chart_name}-{user.slug}',
            f'{settings.HELM_REPO}/{tool.chart_name}',  # XXX assumes repo name
            # f'--version', tool.version,
            f'--namespace', user.k8s_namespace,
            *set_values,
        )

    except HelmError as error:
        raise ToolDeploymentError(error)


def list_tool_deployments(user, id_token=None, search_name=None, search_version=None):
    deployments = []
    k8s = KubernetesClient(id_token=id_token)
    results = k8s.AppsV1Api.list_namespaced_deployment(user.k8s_namespace)
    for deployment in results.items:
        app_name = deployment.metadata.labels["app"]
        _, version = deployment.metadata.labels["chart"].rsplit("-", 1)
        if search_name and search_name not in app_name:
            continue
        if search_version and not version.startswith(search_version):
            continue
        deployments.append(deployment)
    return deployments


def get_tool_deployment(tool_deployment, **kwargs):
    deployments = list_tool_deployments(
        tool_deployment.user,
        search_name=tool_deployment.tool.chart_name,
        # search_version=tool_deployment.tool.version,
        **kwargs,
    )

    if not deployments:
        raise ObjectDoesNotExist(tool_deployment)

    if len(deployments) > 1:
        log.warning(f"Multiple matches for {tool_deployment!r} found")
        raise MultipleObjectsReturned(tool_deployment)

    return deployments[0]


def delete_tool_deployment(tool_deployment, **kwargs):
    deployment = get_tool_deployment(tool_deployment, **kwargs)
    helm.delete(
        deployment.metadata.name,
        f"--namespace={tool_deployment.user.k8s_namespace}",
    )


def get_tool_deployment_status(tool_deployment, **kwargs):
    try:
        deployment = get_tool_deployment(tool_deployment, **kwargs)

    except ObjectDoesNotExist:
        log.warning(f"{tool_deployment} not found")
        return TOOL_NOT_DEPLOYED

    except MultipleObjectsReturned:
        log.warning(f"Multiple objects returned for {tool_deployment}")
        return TOOL_STATUS_UNKNOWN

    conditions = {
        condition.type: condition
        for condition in deployment.status.conditions
    }

    if 'Available' in conditions:
        if conditions['Available'].status == 'True':
            if deployment.spec.replicas == 0:
                return TOOL_IDLED
            return TOOL_READY

    if 'Progressing' in conditions:
        progressing_status = conditions['Progressing'].status
        if progressing_status == 'True':
            return TOOL_DEPLOYING
        elif progressing_status == 'False':
            return TOOL_DEPLOY_FAILED

    log.warning(
        f"Unknown status for {tool_deployment}: {deployment.status.conditions}"
    )
    return TOOL_STATUS_UNKNOWN

def restart_tool_deployment(tool_deployment, id_token=None):
    k8s = KubernetesClient(id_token=id_token)
    return k8s.AppsV1Api.delete_collection_namespaced_replica_set(
        tool_deployment.user.k8s_namespace,
        label_selector=(
            f'app={tool_deployment.tool.chart_name}'
            # f'-{tool_deployment.tool.version}'
        ),
    )
