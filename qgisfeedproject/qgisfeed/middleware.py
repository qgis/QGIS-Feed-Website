import typing

from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from qgisfeed.utils import simplify
from user_visit.middleware import UserVisitMiddleware, save_user_visit
from user_visit.models import UserVisit


class QgisFeedUserVisitMiddleware(UserVisitMiddleware):
    """Middleware to record user visits."""

    def __call__(self, request: HttpRequest) -> typing.Optional[HttpResponse]:
        if request.user.is_anonymous:
            user, _ = User.objects.get_or_create(username="qgis_user")
            request.user = user
            if not request.session or not request.session.session_key:
                request.session.save()

        uv = UserVisit.objects.build(request, timezone.now())
        if not UserVisit.objects.filter(hash=uv.hash).exists():
            try:
                uv.ua_string = simplify(uv.ua_string)
            except:  # noqa
                pass
            if "QGIS" in uv.ua_string:
                save_user_visit(uv)

        return self.get_response(request)
