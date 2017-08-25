from django.db.utils import IntegrityError
from django.test import TestCase, TransactionTestCase

from control_panel_api.models import (
    Role,
    Team,
    TeamMembership,
    User,
)


class MembershipsTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Users
        cls.user_alice, _ = User.objects.get_or_create(username="Alice")
        cls.user_bob, _ = User.objects.get_or_create(username="Bob")
        cls.user_other, _ = User.objects.get_or_create(username="Other")
        # Teams
        cls.team_justice, _ = Team.objects.get_or_create(name="Justice Team")
        cls.team_other, _ = Team.objects.get_or_create(name="Other Team")
        # Roles
        cls.role_maintainer, _ = Role.objects.get_or_create(
            code="maintainer",
            name="Maintainer",
        )
        cls.role_member, _ = Role.objects.get_or_create(
            code="member",
            name="Member",
        )

        # Add Alice to Justice team as maintainer
        TeamMembership.objects.create(
            team=cls.team_justice,
            user=cls.user_alice,
            role=cls.role_maintainer,
        )
        # Add Bob to Justice team as member
        TeamMembership.objects.create(
            team=cls.team_justice,
            user=cls.user_bob,
            role=cls.role_member,
        )
        # Add other user to other team as member
        TeamMembership.objects.create(
            team=cls.team_other,
            user=cls.user_other,
            role=cls.role_member,
        )

    def test_get_user_teams(self):
        alices_teams = self.user_alice.teams()

        self.assertIn(self.team_justice, alices_teams)
        self.assertNotIn(self.team_other, alices_teams)

    def test_get_users_in_a_team(self):
        justice_users = self.team_justice.users()

        self.assertIn(self.user_alice, justice_users)
        self.assertIn(self.user_bob, justice_users)
        self.assertNotIn(self.user_other, justice_users)

    def test_get_users_with_a_role_in_team(self):
        justice_maintainers = self.team_justice.users_with_role("maintainer")
        justice_members = self.team_justice.users_with_role("member")

        self.assertIn(self.user_alice, justice_maintainers)
        self.assertNotIn(self.user_alice, justice_members)
        self.assertNotIn(self.user_bob, justice_maintainers)
        self.assertIn(self.user_bob, justice_members)
        self.assertNotIn(self.user_other, justice_maintainers)
        self.assertNotIn(self.user_other, justice_members)

    def test_user_can_be_added_to_team_only_once(self):
        raised_integrity_error = False

        try:
            # (trying to) Add Alice to team justice again
            TeamMembership.objects.create(
                team=self.team_justice,
                user=self.user_alice,
                role=self.role_member,
            )
        except IntegrityError:
            raised_integrity_error = True

        assert raised_integrity_error
