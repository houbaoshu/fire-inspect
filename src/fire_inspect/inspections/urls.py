from rest_framework.routers import SimpleRouter

from fire_inspect.inspections.views import InspectionViewSet

router = SimpleRouter(trailing_slash=False)
router.register("inspections", InspectionViewSet, basename="inspection")
router.trailing_slash = "/?"
urlpatterns = router.urls
