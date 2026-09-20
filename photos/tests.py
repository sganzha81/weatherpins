from datetime import datetime
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone
from django.urls import reverse
from PIL import Image

from .models import Photo, SiteSettings
from .views import validate_uploaded_photo


class UserPhotosPublicFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="password")
        self.other_user = User.objects.create_user(username="alex", password="password")

    def create_photo(self, user, filename, is_public=False):
        photo = Photo(
            user=user,
            image=f"photos/tests/{filename}",
            is_public=is_public,
            file_size=18,
        )
        Photo.objects.bulk_create([photo])
        return photo

    def test_public_filter_counts_and_limits_photos_to_current_user(self):
        public_photo = self.create_photo(self.user, "public.jpg", is_public=True)
        private_photo = self.create_photo(self.user, "private.jpg", is_public=False)
        self.create_photo(self.other_user, "other-public.jpg", is_public=True)

        self.client.force_login(self.user)

        response = self.client.get(reverse("user_photos"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filter_counts"]["public"], 1)
        self.assertEqual(response.context["public_photo_count"], 1)
        self.assertContains(response, "Публичные:")
        self.assertContains(response, '<span data-public-count>1</span>', html=True)
        self.assertContains(response, "🌍 Публичное")
        self.assertContains(response, "🔒 Приватное")

        response = self.client.get(f"{reverse('user_photos')}?status=public")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_status"], "public")
        self.assertEqual(list(response.context["photos"]), [public_photo])
        self.assertNotIn(private_photo, response.context["photos"])

    def test_public_filter_empty_state(self):
        self.create_photo(self.user, "private.jpg", is_public=False)

        self.client.force_login(self.user)
        response = self.client.get(f"{reverse('user_photos')}?status=public")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "У вас пока нет публичных фото.")
        self.assertContains(
            response,
            "Отметьте фото как публичные, чтобы они появились на публичной карте.",
        )


class TogglePhotoPublicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="password")
        self.other_user = User.objects.create_user(username="alex", password="password")

    def create_photo(self, user, filename, is_public=False):
        photo = Photo(
            user=user,
            image=f"photos/tests/{filename}",
            is_public=is_public,
            file_size=18,
        )
        Photo.objects.bulk_create([photo])
        return photo

    def test_authenticated_user_can_make_own_photo_public(self):
        photo = self.create_photo(self.user, "private.jpg", is_public=False)
        self.client.force_login(self.user)

        response = self.client.post(reverse("toggle_photo_public", args=[photo.pk]))

        photo.refresh_from_db()
        self.assertRedirects(response, reverse("user_photos"))
        self.assertTrue(photo.is_public)

    def test_repeated_post_makes_photo_private_again(self):
        photo = self.create_photo(self.user, "public.jpg", is_public=True)
        self.client.force_login(self.user)

        response = self.client.post(reverse("toggle_photo_public", args=[photo.pk]))

        photo.refresh_from_db()
        self.assertRedirects(response, reverse("user_photos"))
        self.assertFalse(photo.is_public)

    def test_ajax_post_returns_public_toggle_json(self):
        photo = self.create_photo(self.user, "private.jpg", is_public=False)
        self.create_photo(self.user, "already-public.jpg", is_public=True)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("toggle_photo_public", args=[photo.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        photo.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertTrue(photo.is_public)

        data = response.json()
        self.assertEqual(
            data,
            {
                "success": True,
                "photo_id": photo.id,
                "is_public": True,
                "public_count": 2,
                "message": "Фото теперь отображается на публичной карте.",
            },
        )

    def test_ajax_post_returns_private_toggle_json(self):
        photo = self.create_photo(self.user, "public.jpg", is_public=True)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("toggle_photo_public", args=[photo.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        photo.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(photo.is_public)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["photo_id"], photo.id)
        self.assertFalse(data["is_public"])
        self.assertEqual(data["public_count"], 0)
        self.assertEqual(data["message"], "Фото скрыто с публичной карты.")

    def test_user_cannot_toggle_another_users_photo(self):
        photo = self.create_photo(self.other_user, "other-public.jpg", is_public=True)
        self.client.force_login(self.user)

        response = self.client.post(reverse("toggle_photo_public", args=[photo.pk]))

        photo.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertTrue(photo.is_public)

    def test_safe_next_redirect_is_preserved(self):
        photo = self.create_photo(self.user, "private.jpg", is_public=False)
        self.client.force_login(self.user)
        next_url = "/photos/my/?status=public"

        response = self.client.post(
            reverse("toggle_photo_public", args=[photo.pk]),
            {"next": next_url},
        )

        self.assertRedirects(response, next_url)

    def test_unsafe_next_redirect_is_ignored(self):
        photo = self.create_photo(self.user, "private.jpg", is_public=False)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("toggle_photo_public", args=[photo.pk]),
            {"next": "https://evil.com/"},
        )

        self.assertRedirects(response, reverse("user_photos"))

    def test_user_photos_page_shows_public_toggle_switches_in_status_row(self):
        self.create_photo(self.user, "private.jpg", is_public=False)
        self.create_photo(self.user, "public.jpg", is_public=True)
        self.client.force_login(self.user)

        response = self.client.get(reverse("user_photos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "status-row")
        self.assertContains(response, 'class="public-toggle-form"', count=2)
        self.assertContains(response, "data-public-toggle-form")
        self.assertContains(response, "data-visibility-badge")
        self.assertContains(response, "data-public-switch")
        self.assertContains(response, "data-public-count")
        self.assertContains(response, "active-status")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "На публичной карте", count=2)
        self.assertContains(response, "Фото отображается на вашей публичной карте")
        self.assertContains(response, "Фото скрыто с вашей публичной карты")
        self.assertContains(response, "Показывать фото на публичной карте")
        self.assertContains(response, "Скрыть фото с публичной карты")
        self.assertContains(response, "public-switch-on")
        self.assertContains(response, "public-switch-off")
        self.assertNotContains(response, "Сделать публичным")
        self.assertNotContains(response, "Скрыть с публичной карты")


class CalculateClimateNormAjaxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="password")
        self.other_user = User.objects.create_user(username="alex", password="password")

    def create_photo(self, user, filename, **overrides):
        defaults = {
            "user": user,
            "image": f"photos/tests/{filename}",
            "file_size": 18,
            "latitude": 53.1959,
            "longitude": 50.1002,
            "taken_at": timezone.make_aware(datetime(2024, 5, 10, 12, 0)),
            "weather_data": {"source": "hourly", "temperature": 18.0},
        }
        defaults.update(overrides)
        photo = Photo(**defaults)
        Photo.objects.bulk_create([photo])
        return photo

    @patch("photos.views.fetch_climate_comparison_for_photo")
    def test_ajax_post_returns_climate_json(self, fetch_climate):
        photo = self.create_photo(self.user, "with-weather.jpg")
        fetch_climate.return_value = {
            "normal_temperature": 12.345,
            "temperature_anomaly": 5.655,
            "comparison_text": "Теплее обычного.",
            "years_used": [2021, 2022, 2023],
        }
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("calculate_climate_norm", args=[photo.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        photo.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(photo.climate_data, fetch_climate.return_value)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["photo_id"], photo.id)
        self.assertEqual(data["climate_data"], fetch_climate.return_value)
        self.assertEqual(data["climate_display"]["normal_temperature"], "+12.3 °C")
        self.assertEqual(data["climate_display"]["comparison_text"], "Теплее обычного.")
        self.assertEqual(data["climate_display"]["years_used"], [2021, 2022, 2023])
        self.assertEqual(data["message"], "Климатическая норма рассчитана.")

    @patch("photos.views.fetch_climate_comparison_for_photo")
    def test_regular_post_still_redirects(self, fetch_climate):
        photo = self.create_photo(self.user, "with-weather.jpg")
        fetch_climate.return_value = {
            "normal_temperature": 12.0,
            "comparison_text": "Обычная погода.",
        }
        self.client.force_login(self.user)

        response = self.client.post(reverse("calculate_climate_norm", args=[photo.pk]))

        self.assertRedirects(response, reverse("user_photos"))

    def test_user_cannot_calculate_climate_for_another_users_photo(self):
        photo = self.create_photo(self.other_user, "other.jpg")
        self.client.force_login(self.user)

        response = self.client.post(reverse("calculate_climate_norm", args=[photo.pk]))

        self.assertEqual(response.status_code, 404)

    def test_user_photos_page_has_climate_ajax_hooks(self):
        self.create_photo(self.user, "with-weather.jpg", climate_data=None)
        self.client.force_login(self.user)

        response = self.client.get(reverse("user_photos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="climate-form"')
        self.assertContains(response, "data-climate-form")
        self.assertContains(response, "data-climate-feedback")
        self.assertContains(response, "data-photo-meta")
        self.assertContains(response, "csrfmiddlewaretoken")


class EditPhotoNextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="password")

    def create_photo(self, filename, **overrides):
        defaults = {
            "user": self.user,
            "image": f"photos/tests/{filename}",
            "file_size": 18,
            "latitude": 53.1959,
            "longitude": 50.1002,
            "taken_at": timezone.make_aware(datetime(2024, 5, 10, 12, 0)),
            "weather_data": {"source": "hourly", "temperature": 18.0},
        }
        defaults.update(overrides)
        photo = Photo(**defaults)
        Photo.objects.bulk_create([photo])
        return photo

    def edit_payload(self, next_url=None):
        payload = {
            "taken_at": "2024-05-10T12:00",
            "latitude": "53.1959",
            "longitude": "50.1002",
            "is_public": "on",
        }
        if next_url is not None:
            payload["next"] = next_url
        return payload

    def test_user_photos_cards_have_anchor_and_edit_next(self):
        photo = self.create_photo("public.jpg", is_public=True)
        self.client.force_login(self.user)

        response = self.client.get(f"{reverse('user_photos')}?status=public")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="photo-{photo.id}"')
        self.assertContains(response, f"data-photo-id=\"{photo.id}\"")
        self.assertContains(
            response,
            f"{reverse('edit_photo', args=[photo.id])}?next=/photos/my/%3Fstatus%3Dpublic%23photo-{photo.id}",
        )

    @patch("photos.views.fetch_weather_for_photo")
    def test_edit_photo_post_redirects_to_safe_next(self, fetch_weather):
        photo = self.create_photo("photo.jpg")
        fetch_weather.return_value = {"source": "hourly", "temperature": 19.0}
        self.client.force_login(self.user)
        next_url = f"/photos/my/?status=public#photo-{photo.id}"

        response = self.client.post(
            reverse("edit_photo", args=[photo.id]),
            self.edit_payload(next_url),
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    @patch("photos.views.fetch_weather_for_photo")
    def test_edit_photo_post_ignores_unsafe_next(self, fetch_weather):
        photo = self.create_photo("photo.jpg")
        fetch_weather.return_value = {"source": "hourly", "temperature": 19.0}
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("edit_photo", args=[photo.id]),
            self.edit_payload("https://evil.com/"),
        )

        self.assertRedirects(response, reverse("user_photos"))

    def test_edit_page_contains_hidden_next_and_cancel_link(self):
        photo = self.create_photo("photo.jpg")
        self.client.force_login(self.user)
        next_url = f"/photos/my/#photo-{photo.id}"

        response = self.client.get(
            f"{reverse('edit_photo', args=[photo.id])}?next=%2Fphotos%2Fmy%2F%23photo-{photo.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<input type="hidden" name="next" value="{next_url}">',
            html=True,
        )
        self.assertContains(response, f'href="{next_url}"')


class MapImageLightboxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="password")

    def assert_lightbox_markup(self, response):
        self.assertContains(response, "data-image-lightbox")
        self.assertContains(response, "data-lightbox-image-target")
        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, "photos/js/image_lightbox.js")
        self.assertContains(response, "data-lightbox-src")
        self.assertContains(response, "popup-photo-open")

    def test_private_map_contains_shared_lightbox_for_authenticated_user(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("photo_list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "photos/photo_list.html")
        self.assertTemplateUsed(response, "photos/includes/image_lightbox.html")
        self.assert_lightbox_markup(response)
        self.assertContains(response, 'class="popup-actions"')
        self.assertContains(response, 'class="edit-btn"')
        self.assertContains(response, 'class="delete-btn"')

    def test_public_map_contains_shared_lightbox_without_authentication(self):
        response = self.client.get(reverse("public_user_map", args=[self.user.username]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "photos/public_map.html")
        self.assertTemplateUsed(response, "photos/includes/image_lightbox.html")
        self.assert_lightbox_markup(response)
        self.assertNotContains(response, 'class="popup-actions"')
        self.assertNotContains(response, 'class="edit-btn"')
        self.assertNotContains(response, 'class="delete-btn"')


class PublicMapPresentationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="olga",
            password="password",
            first_name="Ольга",
            last_name="Климова",
        )
        self.other_user = User.objects.create_user(
            username="alex",
            password="password",
        )

    def create_public_photo(self, filename="public.jpg"):
        photo = Photo(
            user=self.owner,
            image=f"photos/tests/{filename}",
            is_public=True,
            file_size=18,
            latitude=55.75,
            longitude=37.62,
        )
        Photo.objects.bulk_create([photo])
        return photo

    def public_map(self):
        return self.client.get(
            reverse("public_user_map", args=[self.owner.username])
        )

    def test_public_map_is_available_anonymously_without_owner_links(self):
        response = self.public_map()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Фотокарта @olga")
        self.assertNotContains(response, "Вернуться к моим фото")
        self.assertNotContains(response, "Перейти к моим фото")

    def test_authenticated_owner_sees_owner_links(self):
        self.client.force_login(self.owner)

        response = self.public_map()

        self.assertContains(response, "← Вернуться к моим фото")
        self.assertContains(response, "Перейти к моим фото")
        self.assertContains(response, reverse("user_photos"), count=2)

    def test_authenticated_other_user_does_not_see_owner_links(self):
        self.client.force_login(self.other_user)

        response = self.public_map()

        self.assertNotContains(response, "Вернуться к моим фото")
        self.assertNotContains(response, "Перейти к моим фото")

    def test_allowed_full_name_is_shown_with_username(self):
        self.owner.profile.show_full_name_on_public_map = True
        self.owner.profile.save(update_fields=["show_full_name_on_public_map"])

        response = self.public_map()

        self.assertContains(response, "Фотокарта Ольга Климова")
        self.assertContains(response, "@olga")

    def test_private_full_name_is_not_shown(self):
        response = self.public_map()

        self.assertNotContains(response, "Ольга Климова")
        self.assertContains(response, "Фотокарта @olga")

    def test_public_map_metadata_uses_username_and_canonical_url(self):
        response = self.public_map()
        public_map_url = response.context["public_map_url"]
        expected_title = "@olga — фотокарта в Weatherpins"
        expected_image_alt = "Фотокарта @olga в Weatherpins"

        self.assertContains(
            response,
            f'<link rel="canonical" href="{public_map_url}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta property="og:url" content="{public_map_url}">',
            html=True,
        )
        self.assertContains(response, '<meta property="og:locale" content="ru_RU">', html=True)
        self.assertContains(response, f'<title>{expected_title}</title>', html=True)
        self.assertContains(
            response,
            f'<meta property="og:title" content="{expected_title}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta name="twitter:title" content="{expected_title}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta property="og:image:alt" content="{expected_image_alt}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image:alt" content="{expected_image_alt}">',
            html=True,
        )
        self.assertTrue(
            response.context["og_image_url"].endswith(
                "/static/photos/brand/weatherpins-icon-512.png"
            )
        )
        self.assertContains(
            response,
            f'<meta property="og:image" content="{response.context["og_image_url"]}">',
            html=True,
        )
        self.assertNotContains(response, "Ольга Климова")

    def test_public_map_metadata_uses_allowed_full_name(self):
        self.owner.profile.show_full_name_on_public_map = True
        self.owner.profile.save(update_fields=["show_full_name_on_public_map"])

        response = self.public_map()
        expected_title = "Ольга Климова — фотокарта в Weatherpins"
        expected_image_alt = "Фотокарта Ольга Климова в Weatherpins"

        self.assertContains(response, f'<title>{expected_title}</title>', html=True)
        self.assertContains(
            response,
            f'<meta property="og:title" content="{expected_title}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta name="twitter:title" content="{expected_title}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta property="og:image:alt" content="{expected_image_alt}">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image:alt" content="{expected_image_alt}">',
            html=True,
        )

    def test_empty_map_uses_metadata_description_fallback(self):
        response = self.public_map()

        self.assertEqual(
            response.context["og_description"],
            "У пользователя пока нет публичных фото в Weatherpins.",
        )

    def test_single_photo_uses_singular_metadata_description(self):
        self.create_public_photo()

        response = self.public_map()

        self.assertIn(
            "1 публичное фото на карте",
            response.context["og_description"],
        )

    def test_multiple_photos_use_plural_metadata_description(self):
        self.create_public_photo("first.jpg")
        self.create_public_photo("second.jpg")

        response = self.public_map()

        self.assertIn(
            "2 публичных фото на карте",
            response.context["og_description"],
        )

    def test_anonymous_empty_state_has_no_private_instructions(self):
        response = self.public_map()

        self.assertContains(
            response,
            "На этой карте пока нет публичных фотографий.",
        )
        self.assertNotContains(response, "разделе «Мои фото»")
        self.assertNotContains(response, "Перейти к моим фото")

    def test_owner_empty_state_has_private_instructions_and_link(self):
        self.client.force_login(self.owner)

        response = self.public_map()

        self.assertContains(response, "разделе «Мои фото»")
        self.assertContains(response, "Перейти к моим фото")

    def test_single_photo_uses_singular_count_and_popup_has_no_private_actions(self):
        self.create_public_photo()

        response = self.public_map()

        self.assertContains(response, "1 публичное фото")
        self.assertNotContains(response, 'class="popup-actions"')
        self.assertNotContains(response, 'class="edit-btn"')
        self.assertNotContains(response, 'class="delete-btn"')


class UploadPhotoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sam", password="password")
        self.media_directory = TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.addCleanup(self.media_directory.cleanup)

    def make_jpeg(self, name, color="red"):
        data = BytesIO()
        Image.new("RGB", (20, 20), color=color).save(data, format="JPEG")
        return SimpleUploadedFile(name, data.getvalue(), content_type="image/jpeg")

    def make_large_jpeg(self, name):
        data = BytesIO()
        Image.effect_noise((1000, 1000), 100).save(data, format="JPEG", quality=90)
        return SimpleUploadedFile(name, data.getvalue(), content_type="image/jpeg")

    def upload(self, *files, follow=False):
        return self.client.post(
            reverse("upload_photo"),
            {"image": list(files)},
            follow=follow,
        )

    def test_upload_page_allows_selecting_multiple_files(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("upload_photo"))

        self.assertContains(response, 'name="image"')
        self.assertContains(response, "multiple")
        self.assertContains(response, "Можно выбрать несколько фотографий одновременно.")
        self.assertContains(response, "photos/js/upload-queue.js")
        self.assertContains(response, "data-upload-preview-list")
        self.assertContains(response, "data-upload-submit")
        self.assertContains(response, "data-upload-picker")
        self.assertContains(response, "Выбрать фото")
        self.assertContains(response, "Фото для загрузки")
        self.assertContains(response, "Выбрано: 0")
        self.assertNotContains(response, "Добавить ещё фото")

    @patch("photos.views.Image.open")
    def test_validation_accepts_mpo_jpeg_detected_by_pillow(self, image_open):
        image_open.return_value.format = "MPO"
        uploaded_file = SimpleUploadedFile(
            "iphone.jpg", b"jpeg bytes", content_type="image/jpeg"
        )

        validate_uploaded_photo(uploaded_file)

        image_open.return_value.verify.assert_called_once_with()

    def test_single_file_upload_still_saves_photo_and_redirects_to_my_photos(self):
        self.client.force_login(self.user)

        response = self.upload(self.make_jpeg("single.jpg"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Photo.objects.filter(user=self.user).count(), 1)
        self.assertContains(response, "Загружено 1 фото.")

    def test_multiple_valid_files_are_saved_for_current_user(self):
        self.client.force_login(self.user)

        response = self.upload(
            self.make_jpeg("first.jpg", "red"),
            self.make_jpeg("second.jpg", "blue"),
            follow=True,
        )

        photos = Photo.objects.filter(user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(photos.count(), 2)
        self.assertFalse(Photo.objects.exclude(user=self.user).exists())
        self.assertContains(response, "Загружено 2 фото.")

    def test_mixed_batch_keeps_valid_files_and_reports_corrupt_file(self):
        self.client.force_login(self.user)
        broken_file = SimpleUploadedFile(
            "broken.jpg", b"this is not an image", content_type="image/jpeg"
        )

        response = self.upload(
            self.make_jpeg("first.jpg", "red"),
            self.make_jpeg("second.jpg", "blue"),
            broken_file,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Photo.objects.filter(user=self.user).count(), 2)
        self.assertContains(response, "Загружено 2 фото.")
        self.assertContains(response, "Не удалось обработать 1 фото.")
        self.assertContains(response, "broken.jpg — Не удалось прочитать изображение.")

    def test_photo_without_gps_or_taken_at_is_successful_upload(self):
        self.client.force_login(self.user)

        response = self.upload(self.make_jpeg("no-metadata.jpg"), follow=True)

        photo = Photo.objects.get(user=self.user)
        self.assertIsNone(photo.latitude)
        self.assertIsNone(photo.longitude)
        self.assertIsNone(photo.taken_at)
        self.assertContains(response, "1 фото требуют указать место.")
        self.assertContains(response, "1 фото без даты съёмки.")

    def test_upload_requires_login(self):
        response = self.upload(self.make_jpeg("anonymous.jpg"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("upload_photo"), response.url)
        self.assertEqual(Photo.objects.count(), 0)

    def test_batch_respects_remaining_storage_without_rolling_back_first_file(self):
        SiteSettings.objects.create(user_storage_limit_mb=1)
        first_file = self.make_large_jpeg("first.jpg")
        second_file = self.make_large_jpeg("second.jpg")
        self.assertGreater(first_file.size, 512 * 1024)
        self.assertLess(first_file.size, 1024 * 1024)
        self.assertGreater(second_file.size, 512 * 1024)
        self.assertLess(second_file.size, 1024 * 1024)
        self.client.force_login(self.user)

        response = self.upload(first_file, second_file, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Photo.objects.filter(user=self.user).count(), 1)
        self.assertContains(response, "Загружено 1 фото.")
        self.assertContains(response, "second.jpg — Недостаточно места для загрузки.")
