from contextlib import contextmanager
import logging

from botocore.exceptions import ClientError
from django.conf import settings

from control_panel_api.aws import (
    aws,
    S3AccessPolicy,
)


S3_POLICY_NAME = 's3-access'

READWRITE = 'readwrite'
READONLY = 'readonly'

logger = logging.getLogger(__name__)


def ignore_aws_exceptions(func):
    """Decorates a function to catch and allow exceptions that are thrown for
    existing entities or already created buckets etc, and reraise all others
    """
    exception_names = (
        'BucketAlreadyOwnedByYou',
        'EntityAlreadyExistsException',
        'NoSuchEntityException',
    )

    def inner(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except ClientError as e:
            if e.__class__.__name__ not in exception_names:
                raise e

            logger.error(f"Caught aws exception and ignored: {e}")

    return inner


def _policy_name(bucket_name, readwrite=False):
    """
    Prefix the policy name with bucket name, postfix with access level
    eg: dev-james-readwrite
    """
    return "{}-{}".format(bucket_name, READWRITE if readwrite else READONLY)


def _policy_arn(bucket_name, readwrite=False):
    """
    Return full bucket policy arn
    eg: arn:aws:iam::1337:policy/bucketname-readonly
    """
    return "{}:policy/{}".format(
        settings.IAM_ARN_BASE,
        _policy_name(bucket_name, readwrite))


@ignore_aws_exceptions
def create_role(role_name, add_saml_statement=False):
    """See: `sts:AssumeRole` required by kube2iam
    https://github.com/jtblin/kube2iam#iam-roles"""

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "ec2.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            },
            {
                "Effect": "Allow",
                "Principal": {
                    "AWS":
                        f"{settings.IAM_ARN_BASE}:role/"
                        f"{settings.K8S_WORKER_ROLE_NAME}",
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }

    if add_saml_statement:
        saml_statement = {
            "Effect": "Allow",
            "Principal": {
                "Federated":
                    f"{settings.IAM_ARN_BASE}:saml-provider/"
                    f"{settings.SAML_PROVIDER}"
            },
            "Action": "sts:AssumeRoleWithSAML",
            "Condition": {
                "StringEquals": {
                    "SAML:aud": "https://signin.aws.amazon.com/saml"
                }
            }
        }
        role_policy['Statement'].append(saml_statement)

    aws.create_role(role_name, role_policy)


@ignore_aws_exceptions
def delete_role(role_name):
    aws.delete_role(role_name)


def get_policy_document(bucket_name_arn, readwrite):
    statements = [
        {
            "Sid": "ListBucketsInConsole",
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketLocation",
                "s3:ListAllMyBuckets"
            ],
            "Resource": "arn:aws:s3:::*"
        },
        {
            "Sid": "ListObjects",
            "Action": [
                "s3:ListBucket"
            ],
            "Effect": "Allow",
            "Resource": [bucket_name_arn],
        },
        {
            "Sid": "ReadObjects",
            "Action": [
                "s3:GetObject",
                "s3:GetObjectAcl",
                "s3:GetObjectVersion",
            ],
            "Effect": "Allow",
            "Resource": "{}/*".format(bucket_name_arn)
        },
    ]

    if readwrite:
        statements.append(
            {
                "Sid": "UpdateRenameAndDeleteObjects",
                "Action": [
                    "s3:DeleteObject",
                    "s3:DeleteObjectVersion",
                    "s3:PutObject",
                    "s3:PutObjectAcl",
                    "s3:RestoreObject",
                ],
                "Effect": "Allow",
                "Resource": "{}/*".format(bucket_name_arn)
            }
        )

    return {
        "Version": "2012-10-17",
        "Statement": statements,
    }


@ignore_aws_exceptions
def create_bucket(bucket_name):
    aws.create_bucket(
        bucket_name,
        region=settings.BUCKET_REGION,
        acl='private')
    aws.put_bucket_logging(
        bucket_name,
        target_bucket=settings.LOGS_BUCKET_NAME,
        target_prefix=f"{bucket_name}/")
    aws.put_bucket_encryption(bucket_name)


@ignore_aws_exceptions
def create_bucket_policies(bucket_name, bucket_arn):
    """Create readwrite and readonly policies for s3 bucket"""
    readwrite = True
    policy_name = _policy_name(bucket_name, readwrite)
    policy_document = get_policy_document(bucket_arn, readwrite)

    aws.create_policy(policy_name, policy_document)

    readwrite = False
    policy_name = _policy_name(bucket_name, readwrite)
    policy_document = get_policy_document(bucket_arn, readwrite)

    aws.create_policy(policy_name, policy_document)


def delete_bucket_policies(bucket_name):
    """
    Delete policy from attached entities first then delete policy, for both
    policy types
    """
    policy_arn_readwrite = _policy_arn(bucket_name, readwrite=True)
    aws.detach_policy_from_entities(policy_arn_readwrite)
    aws.delete_policy(policy_arn_readwrite)

    policy_arn_readonly = _policy_arn(bucket_name, readwrite=False)
    aws.detach_policy_from_entities(policy_arn_readonly)
    aws.delete_policy(policy_arn_readonly)


@contextmanager
def s3_access_policy(role_name):
    policy_document = aws.get_inline_policy_document(
        role_name=role_name,
        policy_name=S3_POLICY_NAME,
    )
    policy = S3AccessPolicy(document=policy_document)

    yield policy

    aws.put_role_policy(
        role_name=role_name,
        policy_name=S3_POLICY_NAME,
        policy_document=policy.document,
    )


def revoke_bucket_access(bucket_arn, role_name):
    with s3_access_policy(role_name) as policy:
        policy.revoke_access(bucket_arn)



def grant_bucket_access(bucket_arn, readwrite, role_name):
    with s3_access_policy(role_name) as policy:
        policy.grant_access(bucket_arn, readwrite=readwrite)
