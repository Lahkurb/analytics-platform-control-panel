import base64
import logging

from django.core.management.base import BaseCommand
from django.db.utils import IntegrityError
from kubernetes import config, client

from control_panel_api.models import User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Import Kubernetes user-secrets data into the User.  Uses KUBECONFIG envvar or defaults to ~/.kube/config"

    USER_SECRETS_NAME = 'user-secrets'

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        config.load_kube_config()
        v1 = client.CoreV1Api()
        results = v1.list_secret_for_all_namespaces()

        user_secrets = [item.data for item in results.items if item.metadata.name == Command.USER_SECRETS_NAME]

        users = []
        for item in user_secrets:
            username = base64.b64decode(item['username'])
            email = base64.b64decode(item['email'])

            try:
                users.append(User.objects.create(
                    username=username,
                    email=email
                ))
                logger.debug("Imported {} {}".format(username, email))
            except IntegrityError:
                pass

        self.stdout.write("Imported {} users".format(len(users)))
