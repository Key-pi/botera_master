from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation


class CookieLocaleMiddleware(LocaleMiddleware):
    """Use the explicit interface-language cookie and otherwise stay Ukrainian."""

    def process_request(self, request) -> None:
        supported_languages = {code for code, _name in settings.LANGUAGES}
        language = request.COOKIES.get(
            settings.LANGUAGE_COOKIE_NAME,
            settings.LANGUAGE_CODE,
        )
        if language not in supported_languages:
            language = settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = language
