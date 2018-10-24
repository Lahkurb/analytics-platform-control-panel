from django.conf.urls import include, url
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from rest_framework import routers
from rest_framework_swagger.views import get_swagger_view

from control_panel_api import views

router = routers.DefaultRouter()
router.register(r'apps', views.AppViewSet)
router.register(r'apps3buckets', views.AppS3BucketViewSet)
router.register(r'groups', views.GroupViewSet)
router.register(r's3buckets', views.S3BucketViewSet)
router.register(r'userapps', views.UserAppViewSet)
router.register(r'users', views.UserViewSet)
router.register(r'users3buckets', views.UserS3BucketViewSet)

schema_view = get_swagger_view(title='Control Panel API')

urlpatterns = [
    url(r'^$', schema_view),
    url(r'^', include(router.urls)),
    url(r'^apps/(?P<pk>[0-9]+)/customers/$', views.AppCustomersAPIView.as_view(), name='appcustomers-list'),
    url(r'^apps/(?P<pk>[0-9]+)/customers/(?P<user_id>[\w\d\|]+)/$', views.AppCustomersDetailAPIView.as_view(), name='appcustomers-detail'),
    url(r'^k8s/.+', views.k8s_api_handler),
    url(r'^tools/(?P<tool_name>[-a-z]+)/deployments/$', views.tool_deployments_list, name='tool-deployments-list'),
    url(r'^tools/$', views.supported_tool_list, name='tool-list'),
    url(r'^auth/', include('rest_framework.urls', namespace='rest_framework'))
]

# serve static files if DEBUG = True
urlpatterns += staticfiles_urlpatterns()
