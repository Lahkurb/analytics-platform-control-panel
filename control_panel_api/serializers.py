from django.contrib.auth.models import Group
from rest_framework import serializers

from control_panel_api.models import (
    App,
    AppS3Bucket,
    S3Bucket,
    User,
    UserApp,
    UserS3Bucket,
)


class GroupSerializer(serializers.ModelSerializer):

    class Meta:
        model = Group
        fields = ('id', 'url', 'name')


class AppS3BucketSerializer(serializers.ModelSerializer):

    class Meta:
        model = AppS3Bucket
        fields = ('id', 'url', 'app', 's3bucket', 'access_level')

    def update(self, instance, validated_data):
        if instance.app != validated_data['app']:
            raise serializers.ValidationError(
                "App is not editable. Create a new record."
            )
        if instance.s3bucket != validated_data['s3bucket']:
            raise serializers.ValidationError(
                "S3Bucket is not editable. Create a new record."
            )

        return super().update(instance, validated_data)


class UserS3BucketSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserS3Bucket
        fields = ('id', 'url', 'user', 's3bucket', 'access_level', 'is_admin')

    def update(self, instance, validated_data):
        if instance.user != validated_data['user']:
            raise serializers.ValidationError(
                "User is not editable. Create a new record."
            )
        if instance.s3bucket != validated_data['s3bucket']:
            raise serializers.ValidationError(
                "S3Bucket is not editable. Create a new record."
            )

        return super().update(instance, validated_data)


class AppSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = App
        fields = (
            'id',
            'url',
            'name',
            'slug',
            'repo_url',
            'iam_role_name',
            'created_by',
        )


class S3BucketSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = S3Bucket
        fields = ('id', 'url', 'name', 'arn', 'created_by')


class AppS3BucketNestedInAppSerializer(serializers.ModelSerializer):
    """Used from within with AppSerializer to not expose app"""
    s3bucket = S3BucketSimpleSerializer()

    class Meta:
        model = AppS3Bucket
        fields = ('id', 'url', 's3bucket', 'access_level')


class AppS3BucketNestedInS3BucketSerializer(serializers.ModelSerializer):
    """Used from within with S3BucketSerializer to not expose s3bucket"""
    app = AppSimpleSerializer()

    class Meta:
        model = AppS3Bucket
        fields = ('id', 'url', 'app', 'access_level')


class AppSerializer(serializers.ModelSerializer):
    apps3buckets = AppS3BucketNestedInAppSerializer(many=True, read_only=True)

    class Meta:
        model = App
        fields = (
            'id',
            'url',
            'name',
            'slug',
            'repo_url',
            'iam_role_name',
            'created_by',
            'apps3buckets',
            'userapps',
        )

    def validate_repo_url(self, value):
        """Normalise repo URLs by removing trailing .git"""
        return value.rsplit(".git", 1)[0]


class S3BucketSerializer(serializers.ModelSerializer):
    apps3buckets = AppS3BucketNestedInS3BucketSerializer(
        many=True, read_only=True)

    class Meta:
        model = S3Bucket
        fields = ('id', 'url', 'name', 'arn', 'apps3buckets', 'created_by')
        read_only_fields = ('apps3buckets', 'created_by')


class UserAppSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserApp
        fields = ('id', 'url', 'app', 'user', 'is_admin')

    def update(self, instance, validated_data):
        if instance.user != validated_data['user']:
            raise serializers.ValidationError(
                "User is not editable. Create a new record."
            )
        if instance.app != validated_data['app']:
            raise serializers.ValidationError(
                "App is not editable. Create a new record."
            )

        return super().update(instance, validated_data)


class UserAppNestedInUserSerializer(serializers.ModelSerializer):
    """Used from within with UserSerializer to explicitly expose the app
    but hide the User
    """
    app = AppSimpleSerializer()

    class Meta:
        model = UserApp
        fields = ('id', 'app', 'is_admin')


class UserS3BucketNestedInUserSerializer(serializers.ModelSerializer):
    """Used from within with UserSerializer to explicitly expose the s3bucket
    but hide the User
    """
    s3bucket = S3BucketSimpleSerializer()

    class Meta:
        model = UserS3Bucket
        fields = ('id', 's3bucket', 'access_level', 'is_admin')


class UserSerializer(serializers.ModelSerializer):
    userapps = UserAppNestedInUserSerializer(many=True, read_only=True)
    users3buckets = UserS3BucketNestedInUserSerializer(
        many=True, read_only=True)

    class Meta:
        model = User
        fields = (
            'auth0_id',
            'url',
            'username',
            'name',
            'email',
            'groups',
            'userapps',
            'users3buckets',
        )
