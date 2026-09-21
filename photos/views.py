from datetime import datetime

import requests
from PIL import Image, UnidentifiedImageError

from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.views.decorators.http import require_POST
from django.templatetags.static import static
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import UserProfile

from .forms import PhotoEditForm
from .climate import fetch_climate_comparison_for_photo
from .file_utils import format_file_size
from .models import Photo
from .site_settings import get_user_storage_limit_bytes
from .weather import fetch_weather_for_photo
from .weather_codes import get_weather_info


# Pillow reports some JPEGs with MPF metadata (including iPhone photos) as MPO.
SUPPORTED_UPLOAD_FORMATS = {"JPEG", "MPO", "PNG", "HEIF", "HEIC"}


def uploaded_filename(uploaded_file):
    """Return a display-safe filename without a client-supplied path."""
    return uploaded_file.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def validate_uploaded_photo(uploaded_file):
    """Check the actual image data before the Photo model processes it."""
    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.verify()
        if image.format not in SUPPORTED_UPLOAD_FORMATS:
            raise ValidationError("Поддерживаются файлы JPG, JPEG, PNG, HEIC и HEIF.")
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as error:
        raise ValidationError("Не удалось прочитать изображение.") from error
    finally:
        uploaded_file.seek(0)


def process_uploaded_photo(user, uploaded_file):
    """Run the existing Photo save pipeline for one uploaded file."""
    validate_uploaded_photo(uploaded_file)
    photo = Photo(user=user, image=uploaded_file)
    photo.save()
    return photo


def format_weather_time(weather_time):
    if not weather_time:
        return None

    try:
        return datetime.strptime(weather_time, "%Y-%m-%dT%H:%M").strftime("%d.%m.%Y, %H:%M")
    except (TypeError, ValueError):
        return weather_time


def format_climate_temperature(value):
    if value is None:
        return None

    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.1f} °C"


def climate_norm_payload(photo, message, success=True):
    climate_data = photo.climate_data or {}
    return {
        "success": success,
        "photo_id": photo.id,
        "climate_data": climate_data,
        "climate_display": {
            "normal_temperature": format_climate_temperature(
                climate_data.get("normal_temperature")
            ),
            "comparison_text": climate_data.get("comparison_text") or "",
            "years_used": climate_data.get("years_used") or [],
        },
        "message": message,
    }


def get_safe_next_url(request, next_url):
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return None


@login_required
def upload_photo(request):
    if request.method == "POST":
        image_files = request.FILES.getlist("image")
        if not image_files:
            messages.error(request, "Пожалуйста, выберите хотя бы один файл.")
            return render(request, "photos/upload.html")

        current_total = (
            Photo.objects.filter(user=request.user).aggregate(total=Sum("file_size"))[
                "total"
            ]
            or 0
        )
        storage_limit = get_user_storage_limit_bytes()
        success_count = 0
        no_geo_count = 0
        no_date_count = 0
        errors = []

        for image_file in image_files:
            filename = uploaded_filename(image_file)
            upload_size = getattr(image_file, "size", 0) or 0

            # Check each file against the remaining space. Successful previous
            # files reduce current_total, so a batch cannot overrun the limit.
            if current_total + upload_size > storage_limit:
                errors.append(
                    (
                        filename,
                        (
                            "Недостаточно места для загрузки. Сейчас занято "
                            f"{format_file_size(current_total)} из "
                            f"{format_file_size(storage_limit)}."
                        ),
                    )
                )
                continue

            try:
                photo = process_uploaded_photo(request.user, image_file)
                saved_size = photo.file_size or upload_size
                if current_total + saved_size > storage_limit:
                    # HEIC conversion can change the stored file size. Do not
                    # retain a converted file that would exceed the limit.
                    photo.delete()
                    errors.append(
                        (
                            filename,
                            "Недостаточно места для загрузки после обработки файла.",
                        )
                    )
                    continue

                success_count += 1
                current_total += saved_size

                if photo.latitude is None or photo.longitude is None:
                    no_geo_count += 1
                if photo.taken_at is None:
                    no_date_count += 1
            except ValidationError as e:
                errors.append((filename, " ".join(e.messages)))

        if success_count:
            messages.success(request, f"Загружено {success_count} фото.")
        if no_geo_count:
            messages.warning(request, f"{no_geo_count} фото требуют указать место.")
        if no_date_count:
            messages.warning(request, f"{no_date_count} фото без даты съёмки.")
        if errors:
            messages.error(request, f"Не удалось обработать {len(errors)} фото.")
        for filename, error in errors:
            messages.error(request, f"{filename} — {error}")

        # Keep a user who has nothing saved on the upload page, but show all
        # successful (including mixed-result) batches in the management view.
        if success_count:
            return redirect("user_photos")
        return render(request, "photos/upload.html")

    return render(request, "photos/upload.html")


@login_required
def photo_list(request):
    # Вся логика по сбору данных теперь в photos_geojson, а здесь просто рендерим шаблон
    return render(request, 'photos/photo_list.html')


def photo_geojson_feature(photo):
    w = photo.weather_data or {}
    weather_info = get_weather_info(w.get("weathercode"))
    weather_source = None
    weather_temperature = None
    weather_temperature_max = None
    weather_temperature_min = None
    weather_precipitation = None
    weather_time = None

    if w.get("source") == "hourly":
        weather_source = "hourly"
        weather_temperature = w.get("temperature")
        weather_precipitation = w.get("precipitation")
        weather_time = w.get("weather_time")
    elif w:
        weather_source = "daily"
        weather_temperature_max = w.get("temperature_max")
        weather_temperature_min = w.get("temperature_min")
        weather_precipitation = w.get("precipitation")

    weather_parts = [f"{weather_info['emoji']} {weather_info['label']}"]
    if weather_source == "hourly" and weather_temperature is not None:
        weather_parts.append(f"{weather_temperature}°C")
    elif weather_source == "daily":
        if weather_temperature_max is not None:
            weather_parts.append(f"Макс {weather_temperature_max}°C")
        if weather_temperature_min is not None:
            weather_parts.append(f"Мин {weather_temperature_min}°C")
    if weather_precipitation is not None:
        weather_parts.append(f"Осадки {weather_precipitation} мм")
    weather_str = " · ".join(weather_parts) if w else "Нет данных"

    taken = photo.taken_at or photo.uploaded_at
    date_str = taken.strftime("%d.%m.%Y %H:%M") if taken else "Неизвестно"
    climate_data = photo.climate_data or {}

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [photo.longitude, photo.latitude],
        },
        "properties": {
            "id": photo.id,
            "date": date_str,
            "weather": weather_str,
            "weather_emoji": weather_info["emoji"],
            "weather_label": weather_info["label"],
            "weather_css_class": weather_info["css_class"],
            "weather_source": weather_source,
            "weather_temperature": weather_temperature,
            "weather_temperature_max": weather_temperature_max,
            "weather_temperature_min": weather_temperature_min,
            "weather_precipitation": weather_precipitation,
            "weather_time": weather_time,
            "weather_time_display": format_weather_time(weather_time),
            "climate_normal_temperature": climate_data.get("normal_temperature"),
            "climate_temperature_anomaly": climate_data.get("temperature_anomaly"),
            "climate_comparison_text": climate_data.get("comparison_text"),
            "climate_source": climate_data.get("source"),
            "image_url": photo.image.url if photo.image else "",
            "is_public": photo.is_public,
        },
    }


def get_place_name(request):
    lat = request.GET.get("lat")
    lon = request.GET.get("lon")
    if not lat or not lon:
        return JsonResponse({"error": "Неверные координаты"}, status=400)
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": lat,
            "lon": lon,
            "format": "jsonv2",
            "zoom": 10,  # уровень детализации: 10 — район/город
            "accept-language": "ru",
        }
        headers = {
            "User-Agent": "PhotoWeatherMap/1.0 (sganzha@gmail.com)"
        }  # укажи свой email
        resp = requests.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        name = data.get("name", "")
        # часто поле 'name' содержит слишком мелкий объект, можно взять более крупный
        display_name = data.get("display_name", name)
        return JsonResponse({"name": name, "display_name": display_name})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@login_required
def photos_geojson(request):
    """Возвращает все фото текущего пользователя в формате GeoJSON."""
    photos = Photo.objects.filter(user=request.user)
    exclude_photo_id = request.GET.get("exclude")
    if exclude_photo_id:
        photos = photos.exclude(pk=exclude_photo_id)

    features = []
    for photo in photos:
        if photo.latitude is None or photo.longitude is None:
            continue

        features.append(photo_geojson_feature(photo))

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }
    return JsonResponse(geojson)


def public_user_map(request, username):
    public_user = get_object_or_404(User, username=username)
    public_user_profile, _ = UserProfile.objects.get_or_create(user=public_user)
    public_user_full_name = public_user.get_full_name()
    show_public_user_full_name = (
        public_user_profile.show_full_name_on_public_map
        and bool(public_user_full_name)
    )
    public_photo_count = Photo.objects.filter(
        user=public_user,
        is_public=True,
        latitude__isnull=False,
        longitude__isnull=False,
    ).count()
    public_map_url = request.build_absolute_uri(request.path)
    if show_public_user_full_name:
        public_map_owner_name = public_user_full_name
    else:
        public_map_owner_name = f"@{public_user.username}"

    og_title = f"{public_map_owner_name} — фотокарта в Weatherpins"
    og_image_alt = f"Фотокарта {public_map_owner_name} в Weatherpins"
    if public_photo_count > 0:
        if public_photo_count == 1:
            public_photo_count_text = "1 публичное фото"
        else:
            public_photo_count_text = f"{public_photo_count} публичных фото"
        og_description = (
            f"{public_photo_count_text} на карте с погодой в момент "
            "съёмки и климатической нормой."
        )
    else:
        public_photo_count_text = "0 публичных фото"
        og_description = "У пользователя пока нет публичных фото в Weatherpins."
    og_image_url = request.build_absolute_uri(
        static("photos/brand/weatherpins-icon-512.png")
    )

    return render(
        request,
        "photos/public_map.html",
        {
            "public_user": public_user,
            "public_user_full_name": public_user_full_name,
            "show_public_user_full_name": show_public_user_full_name,
            "public_photo_count": public_photo_count,
            "public_photo_count_text": public_photo_count_text,
            "public_map_url": public_map_url,
            "og_title": og_title,
            "og_description": og_description,
            "og_image_url": og_image_url,
            "og_image_alt": og_image_alt,
        },
    )


def public_user_geojson(request, username):
    public_user = get_object_or_404(User, username=username)
    photos = Photo.objects.filter(
        user=public_user,
        is_public=True,
        latitude__isnull=False,
        longitude__isnull=False,
    )

    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": [photo_geojson_feature(photo) for photo in photos],
        }
    )

@login_required
@require_POST
def toggle_photo_public(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id, user=request.user)
    photo.is_public = not photo.is_public
    photo.save(update_fields=["is_public"])

    if photo.is_public:
        message = "Фото теперь отображается на публичной карте."
    else:
        message = "Фото скрыто с публичной карты."

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        public_count = Photo.objects.filter(
            user=request.user,
            is_public=True,
        ).count()
        return JsonResponse(
            {
                "success": True,
                "photo_id": photo.id,
                "is_public": photo.is_public,
                "public_count": public_count,
                "message": message,
            }
        )

    messages.success(request, message)

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect("user_photos")


@login_required
def delete_photo(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Проверка, что пользователь — владелец
    if photo.user != request.user:
        if is_ajax:
            return JsonResponse(
                {"success": False, "error": "У вас нет прав на удаление этого фото"},
                status=403,
            )

        messages.error(request, "У вас нет прав на удаление этого фото.")
        return redirect("user_photos")

    if request.method == "POST":
        photo.delete()

        if is_ajax:
            return JsonResponse({
                "success": True,
                "photo_id": photo_id,
            })

        messages.success(request, "Фото удалено.")
        next_url = get_safe_next_url(request, request.POST.get("next"))
        if next_url:
            return redirect(next_url)
        return redirect("user_photos")

    if is_ajax:
        return JsonResponse(
            {"success": False, "error": "Метод не разрешён"},
            status=405,
        )

    messages.error(request, "Метод не разрешён.")
    return redirect("user_photos")
    
@login_required
def user_photos(request):
    active_status = request.GET.get("status", "all")
    base_photos = Photo.objects.filter(user=request.user)
    photos = base_photos.order_by("-uploaded_at", "-pk")
    total_file_size = base_photos.aggregate(total=Sum("file_size"))["total"] or 0
    storage_limit = get_user_storage_limit_bytes()
    storage_usage_percent = (
        round((total_file_size / storage_limit) * 100, 1) if storage_limit else 0
    )
    storage_usage_bar_percent = min(storage_usage_percent, 100)
    storage_usage_bar_percent_css = f"{storage_usage_bar_percent:.1f}".rstrip(
        "0"
    ).rstrip(".")
    storage_usage_status = "ok"
    if storage_usage_percent >= 100:
        storage_usage_status = "full"
    elif storage_usage_percent >= 90:
        storage_usage_status = "warning"

    filter_counts = {
        "all": base_photos.count(),
        "on_map": base_photos.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        ).count(),
        "no_geo": base_photos.filter(
            Q(latitude__isnull=True) | Q(longitude__isnull=True),
        ).count(),
        "no_date": base_photos.filter(taken_at__isnull=True).count(),
        "no_weather": base_photos.filter(weather_data__isnull=True).count(),
        "public": base_photos.filter(is_public=True).count(),
    }

    if active_status == "on_map":
        photos = photos.filter(latitude__isnull=False, longitude__isnull=False)
    elif active_status == "no_geo":
        photos = photos.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True))
    elif active_status == "no_date":
        photos = photos.filter(taken_at__isnull=True)
    elif active_status == "no_weather":
        photos = photos.filter(weather_data__isnull=True)
    elif active_status == "public":
        photos = photos.filter(is_public=True)
    else:
        active_status = "all"

    paginator = Paginator(photos, 24)
    page_obj = paginator.get_page(request.GET.get("page"))

    for photo in page_obj.object_list:
        weather_data = photo.weather_data or {}
        photo.weather_info = get_weather_info(weather_data.get("weathercode"))
        photo.weather_time_display = format_weather_time(weather_data.get("weather_time"))
        photo.file_size_display = format_file_size(photo.file_size)

    public_photo_count = filter_counts["public"]

    public_map_url = request.build_absolute_uri(
        reverse("public_user_map", args=[request.user.username])
    )

    return render(
        request,
        "photos/user_photos.html",
        {
            "photos": page_obj.object_list,
            "page_obj": page_obj,
            "page_range": paginator.get_elided_page_range(
                page_obj.number,
                on_each_side=1,
                on_ends=1,
            ),
            "active_status": active_status,
            "filter_counts": filter_counts,
            "total_file_size_display": format_file_size(total_file_size),
            "total_photo_count": filter_counts["all"],
            "storage_limit_display": format_file_size(storage_limit),
            "storage_used_display": format_file_size(total_file_size),
            "storage_usage_percent": storage_usage_percent,
            "storage_usage_bar_percent_css": storage_usage_bar_percent_css,
            "storage_usage_status": storage_usage_status,
            "public_photo_count": public_photo_count,
            "public_map_url": public_map_url,
        },
    )


@login_required
@require_POST
def refresh_weather(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id, user=request.user)

    if (
        photo.latitude is None
        or photo.longitude is None
        or photo.taken_at is None
    ):
        messages.warning(
            request,
            "Для обновления погоды нужны координаты и дата съёмки.",
        )
        return redirect("user_photos")

    result = fetch_weather_for_photo(
        photo.latitude,
        photo.longitude,
        photo.taken_at,
    )

    if result is not None:
        photo.weather_data = result
        photo.save(update_fields=["weather_data"])
        messages.success(request, "Погода обновлена.")
    else:
        messages.warning(request, "Не удалось получить погоду. Попробуйте позже.")

    return redirect("user_photos")


@login_required
@require_POST
def calculate_climate_norm(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id, user=request.user)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if (
        photo.latitude is None
        or photo.longitude is None
        or photo.taken_at is None
        or not photo.weather_data
    ):
        message = "Для расчёта нормы нужны координаты, дата и погода."
        if is_ajax:
            return JsonResponse(
                {
                    "success": False,
                    "photo_id": photo.id,
                    "climate_data": photo.climate_data or {},
                    "message": message,
                },
                status=400,
            )

        messages.warning(request, message)
        return redirect("user_photos")

    result = fetch_climate_comparison_for_photo(photo)
    if result is not None:
        photo.climate_data = result
        photo.save(update_fields=["climate_data"])
        message = "Климатическая норма рассчитана."
        if is_ajax:
            return JsonResponse(climate_norm_payload(photo, message))

        messages.success(request, message)
    else:
        message = "Не удалось рассчитать климатическую норму. Попробуйте позже."
        if is_ajax:
            return JsonResponse(
                {
                    "success": False,
                    "photo_id": photo.id,
                    "climate_data": photo.climate_data or {},
                    "message": message,
                },
                status=502,
            )

        messages.warning(request, message)

    return redirect("user_photos")


@login_required
def edit_photo(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id, user=request.user)
    next_url = get_safe_next_url(request, request.GET.get("next"))

    if request.method == "POST":
        form = PhotoEditForm(request.POST, instance=photo)
        next_url = get_safe_next_url(request, request.POST.get("next"))

        if form.is_valid():
            photo = form.save(commit=False)

            if (
                photo.latitude is not None
                and photo.longitude is not None
                and photo.taken_at is not None
            ):
                photo.weather_data = fetch_weather_for_photo(
                    photo.latitude,
                    photo.longitude,
                    photo.taken_at,
                )
                messages.success(request, "Фото сохранено, погода обновлена.")
            else:
                if photo.latitude is None or photo.longitude is None:
                    photo.weather_data = None
                    messages.warning(
                        request,
                        "Фото сохранено, но без координат оно не появится на карте.",
                    )
                else:
                    messages.warning(
                        request,
                        "Фото сохранено, но без даты съёмки погоду обновить нельзя.",
                    )

            photo.save()
            if next_url:
                return redirect(next_url)
            return redirect("user_photos")
    else:
        form = PhotoEditForm(instance=photo)

    return render(
        request,
        "photos/edit_photo.html",
        {
            "form": form,
            "photo": photo,
            "next": next_url or "",
            "cancel_url": next_url or reverse("user_photos"),
        },
    )
