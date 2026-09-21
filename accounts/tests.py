from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from photos.models import Photo

from .forms import RegisterForm, USERNAME_ERROR_MESSAGE, UserProfileForm
from .models import UserProfile


class RegisterFormTests(TestCase):
    def form(self, username, email="olga@example.com"):
        return RegisterForm(
            data={
                "username": username,
                "email": email,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            }
        )

    def test_accepts_url_safe_username(self):
        form = self.form("olga-k16")

        self.assertTrue(form.is_valid(), form.errors)

    def test_normalizes_username_to_lowercase(self):
        form = self.form("Olga-K16")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["username"], "olga-k16")

    def test_strips_username_edge_spaces(self):
        form = self.form("  Olga-K16  ")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["username"], "olga-k16")

    def test_rejects_invalid_usernames(self):
        for username in ("Ольга@К16", "@olga", "olga k16", "olga/k16"):
            with self.subTest(username=username):
                form = self.form(username)

                self.assertFalse(form.is_valid())
                self.assertIn(USERNAME_ERROR_MESSAGE, form.errors["username"])

    def test_username_must_be_unique_after_normalization(self):
        User.objects.create_user(
            username="olga-k16",
            email="olga@example.com",
            password="password123",
        )

        form = self.form("Olga-K16")

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Пользователь с таким username уже существует.",
            form.errors["username"],
        )

    def test_email_is_required(self):
        form = self.form("olga-k16", email="")

        self.assertFalse(form.is_valid())
        self.assertIn("Обязательное поле.", form.errors["email"])

    def test_normalizes_email_before_saving(self):
        form = self.form("olga-k16", email="  Test@Example.COM  ")

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, "test@example.com")

    def test_email_must_be_unique_case_insensitively(self):
        User.objects.create_user(
            username="another",
            email="test@example.com",
            password="password123",
        )

        form = self.form("olga-k16", email="Test@Example.COM")

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Пользователь с таким email уже существует.",
            form.errors["email"],
        )


class RegisterViewTests(TestCase):
    def test_post_creates_user_with_normalized_username_and_email(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "  Olga-K16  ",
                "email": "  Olga@Example.COM  ",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("photo_list"))
        user = User.objects.get(username="olga-k16")
        self.assertEqual(user.email, "olga@example.com")


class NormalizedLoginTests(TestCase):
    password = "LoginPass123!"

    def setUp(self):
        self.user = User.objects.create_user(
            username="test_regi",
            email="test_regi@example.com",
            password=self.password,
        )

    def test_login_normalizes_username(self):
        for username in (
            "test_regi",
            "Test_Regi",
            "TEST_REGI",
            "  Test_Regi  ",
        ):
            with self.subTest(username=username):
                self.client.logout()

                response = self.client.post(
                    reverse("login"),
                    {"username": username, "password": self.password},
                )

                self.assertRedirects(response, reverse("photo_list"))
                self.assertEqual(
                    self.client.session.get("_auth_user_id"),
                    str(self.user.pk),
                )

        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "test_regi")

    def test_password_remains_case_sensitive(self):
        response = self.client.post(
            reverse("login"),
            {"username": "Test_Regi", "password": "loginpass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(
            response,
            "Неверное имя пользователя или пароль. Попробуйте ещё раз.",
        )

    def test_nonexistent_username_keeps_generic_login_error(self):
        response = self.client.post(
            reverse("login"),
            {"username": "Missing_User", "password": self.password},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(
            response,
            "Неверное имя пользователя или пароль. Попробуйте ещё раз.",
        )


class UserProfileModelTests(TestCase):
    def test_profile_created_for_new_user(self):
        user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
        )

        self.assertTrue(UserProfile.objects.filter(user=user).exists())

    def test_default_show_full_name_on_public_map_is_false(self):
        user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
        )

        self.assertFalse(user.profile.show_full_name_on_public_map)

    def test_existing_user_profile_can_be_get_or_created(self):
        User.objects.bulk_create(
            [
                User(
                    username="legacy",
                    email="legacy@example.com",
                )
            ]
        )
        user = User.objects.get(username="legacy")

        profile, created = UserProfile.objects.get_or_create(user=user)

        self.assertTrue(created)
        self.assertFalse(profile.show_full_name_on_public_map)


class UserProfileFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
        )

    def form_for_user(self, user, **overrides):
        data = {
            "username": user.username,
            "email": user.email,
            "first_name": "Ольга",
            "last_name": "Климова",
        }
        data.update(overrides)
        return UserProfileForm(data=data, instance=user)

    def test_accepts_url_safe_username(self):
        form = self.form_for_user(self.user, username="olga-k16")

        self.assertTrue(form.is_valid(), form.errors)

    def test_normalizes_username_to_lowercase(self):
        form = self.form_for_user(self.user, username="Olga-K16")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["username"], "olga-k16")

    def test_strips_username_edge_spaces(self):
        form = self.form_for_user(self.user, username="  olga-k16  ")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["username"], "olga-k16")

    def test_rejects_username_with_cyrillic_at_space_or_slash(self):
        for username in ("Ольга@К16", "olga k16", "olga/k16"):
            with self.subTest(username=username):
                form = self.form_for_user(self.user, username=username)

                self.assertFalse(form.is_valid())
                self.assertIn(USERNAME_ERROR_MESSAGE, form.errors["username"])

    def test_username_uniqueness_excludes_current_user(self):
        form = self.form_for_user(self.user, username="olga")

        self.assertTrue(form.is_valid(), form.errors)

    def test_username_must_be_unique_for_other_users(self):
        User.objects.create_user(
            username="taken",
            email="taken@example.com",
            password="password123",
        )

        form = self.form_for_user(self.user, username="taken")

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Пользователь с таким username уже существует.",
            form.errors["username"],
        )

    def test_email_is_required(self):
        form = self.form_for_user(self.user, email="")

        self.assertFalse(form.is_valid())
        self.assertIn("Обязательное поле.", form.errors["email"])

    def test_normalizes_email_to_lowercase(self):
        form = self.form_for_user(self.user, email="Test@Email.COM")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "test@email.com")

    def test_strips_email_edge_spaces(self):
        form = self.form_for_user(self.user, email="  test@email.com  ")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "test@email.com")

    def test_email_must_be_unique_for_other_users(self):
        User.objects.create_user(
            username="another",
            email="Taken@Example.COM",
            password="password123",
        )

        form = self.form_for_user(self.user, email="taken@example.com")

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Пользователь с таким email уже существует.",
            form.errors["email"],
        )

    def test_email_uniqueness_excludes_current_user_case_insensitively(self):
        form = self.form_for_user(self.user, email="Olga@Example.COM")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "olga@example.com")


class ProfileEditViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
        )

    def test_updates_profile_and_redirects_to_profile(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("profile_edit"),
            {
                "username": "  Olga-K16  ",
                "email": "  New@Example.COM  ",
                "first_name": "Ольга",
                "last_name": "Климова",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "olga-k16")
        self.assertEqual(self.user.email, "new@example.com")
        self.assertEqual(self.user.first_name, "Ольга")
        self.assertEqual(self.user.last_name, "Климова")

    def test_saves_show_full_name_on_public_map_true(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("profile_edit"),
            {
                "username": "olga",
                "email": "olga@example.com",
                "first_name": "Ольга",
                "last_name": "Климова",
                "show_full_name_on_public_map": "on",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.show_full_name_on_public_map)

    def test_saves_show_full_name_on_public_map_false(self):
        self.user.profile.show_full_name_on_public_map = True
        self.user.profile.save(update_fields=["show_full_name_on_public_map"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("profile_edit"),
            {
                "username": "olga",
                "email": "olga@example.com",
                "first_name": "Ольга",
                "last_name": "Климова",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.show_full_name_on_public_map)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="olga",
            email="test@example.com",
            password="OldStrongPass123!",
        )

    def test_password_reset_page_is_available(self):
        response = self.client.get(reverse("password_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')

    def test_existing_email_sends_reset_message(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": self.user.email},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Weatherpins", message.subject)
        self.assertIn("Восстановление пароля", message.subject)
        self.assertIn("http://testserver/accounts/reset/", message.body)

    def test_email_lookup_is_case_insensitive(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "Test@Example.COM"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_email_uses_generic_done_page_without_sending_email(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "unknown@example.com"},
            follow=True,
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertContains(
            response,
            "Если аккаунт с указанным email существует, мы отправили ссылку",
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_user_does_not_receive_reset_email(self):
        User.objects.create_user(
            username="inactive",
            email="inactive@example.com",
            password="StrongPass123!",
            is_active=False,
        )

        response = self.client.post(
            reverse("password_reset"),
            {"email": "inactive@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_user_with_unusable_password_does_not_receive_reset_email(self):
        user = User.objects.create_user(
            username="external",
            email="external@example.com",
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        response = self.client.post(
            reverse("password_reset"),
            {"email": "external@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_user_can_set_new_password_with_django_token(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        confirm_url = reverse(
            "password_reset_confirm",
            kwargs={"uidb64": uidb64, "token": token},
        )

        response = self.client.get(confirm_url)
        self.assertRedirects(response, confirm_url.replace(token, "set-password"))

        set_password_url = response.url
        response = self.client.get(set_password_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="new_password1"')
        self.assertContains(response, 'name="new_password2"')

        response = self.client.post(
            set_password_url,
            {
                "new_password1": "NewSecurePass456!",
                "new_password2": "NewSecurePass456!",
            },
        )

        self.assertRedirects(response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewSecurePass456!"))
        self.assertTrue(
            self.client.login(
                username=self.user.username,
                password="NewSecurePass456!",
            )
        )

    def test_invalid_token_offers_a_new_reset_request(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.get(
            reverse(
                "password_reset_confirm",
                kwargs={"uidb64": uidb64, "token": "invalid-token"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Эта ссылка недействительна или устарела")
        self.assertContains(response, "Запросить новую ссылку")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class LegacyUserCompatibilityTests(TestCase):
    def test_user_without_email_can_log_in_and_add_email_in_profile(self):
        user = User.objects.create_user(
            username="legacy",
            email="",
            password="OldStrongPass123!",
        )

        self.assertTrue(
            self.client.login(username="legacy", password="OldStrongPass123!")
        )
        response = self.client.post(
            reverse("profile_edit"),
            {
                "username": "legacy",
                "email": "  Legacy@Example.COM  ",
                "first_name": "",
                "last_name": "",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        user.refresh_from_db()
        self.assertEqual(user.email, "legacy@example.com")

    def test_user_without_email_is_not_exposed_by_password_reset(self):
        User.objects.create_user(
            username="legacy",
            email="",
            password="OldStrongPass123!",
        )

        response = self.client.post(
            reverse("password_reset"),
            {"email": "unknown@example.com"},
            follow=True,
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertContains(response, "Если аккаунт с указанным email существует")
        self.assertEqual(len(mail.outbox), 0)


class AccountTemplateRegressionTests(TestCase):
    def test_login_page_links_to_password_reset(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Забыли пароль?")
        self.assertContains(response, reverse("password_reset"))

    def test_register_page_contains_email_field_and_hint(self):
        response = self.client.get(reverse("register"))

        self.assertContains(response, 'name="email"')
        self.assertContains(response, "Email понадобится для восстановления доступа.")

    def test_password_reset_done_page_uses_generic_wording(self):
        response = self.client.get(reverse("password_reset_done"))

        self.assertContains(response, "Если аккаунт с указанным email существует")

    def test_password_reset_complete_page_links_to_login(self):
        response = self.client.get(reverse("password_reset_complete"))

        self.assertContains(response, "Пароль изменён.")
        self.assertContains(response, reverse("login"))


class LogoutViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="logout-user", password="StrongPass123!"
        )
        self.client.force_login(self.user)

    def test_navigation_uses_csrf_protected_post(self):
        response = self.client.get(reverse("profile"))

        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, '<button type="submit">Выйти</button>', html=True)

    def test_logout_requires_post_and_redirects_after_logout(self):
        logout_url = reverse("logout")

        self.assertEqual(self.client.get(logout_url).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

        response = self.client.post(logout_url)

        self.assertRedirects(
            response, reverse("photo_list"), fetch_redirect_response=False
        )
        self.assertNotIn("_auth_user_id", self.client.session)


class PublicMapNameDisplayTests(TestCase):
    def create_public_photo(self, user):
        return Photo.objects.create(
            user=user,
            latitude=53.2,
            longitude=50.15,
            is_public=True,
        )

    def test_public_map_when_false_shows_only_username(self):
        user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
            first_name="Ольга",
            last_name="Климова",
        )
        self.create_public_photo(user)

        response = self.client.get(reverse("public_user_map", args=[user.username]))

        self.assertContains(response, "@olga")
        self.assertNotContains(response, "Ольга Климова")

    def test_public_map_when_true_and_full_name_shows_full_name_and_username(self):
        user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
            first_name="Ольга",
            last_name="Климова",
        )
        user.profile.show_full_name_on_public_map = True
        user.profile.save(update_fields=["show_full_name_on_public_map"])
        self.create_public_photo(user)

        response = self.client.get(reverse("public_user_map", args=[user.username]))

        self.assertContains(response, "Ольга Климова")
        self.assertContains(response, "@olga")

    def test_public_map_when_true_without_full_name_shows_only_username(self):
        user = User.objects.create_user(
            username="olga",
            email="olga@example.com",
            password="password123",
        )
        user.profile.show_full_name_on_public_map = True
        user.profile.save(update_fields=["show_full_name_on_public_map"])
        self.create_public_photo(user)

        response = self.client.get(reverse("public_user_map", args=[user.username]))

        self.assertContains(response, "@olga")
        self.assertNotContains(response, "public-user-name")
