from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from django.http import FileResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.common.api import ApiError
from fire_inspect.inspections.policies import InspectionAccessPolicy
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.serializers import MaterialSerializer
from fire_inspect.materials.services import (
    controlled_path,
    controlled_storage_key,
    create_text_material,
    create_uploaded_material,
    delete_material,
)
from fire_inspect.materials.validation import UploadValidationError


def material_queryset_for(user):
    return Material.objects.filter(
        inspection__in=InspectionAccessPolicy.queryset_for(user)
    ).select_related("inspection")


def derivative_queryset_for(user):
    return MaterialDerivative.objects.filter(
        material__inspection__in=InspectionAccessPolicy.queryset_for(user)
    ).select_related("material", "material__inspection")


class InspectionMaterialListCreateView(APIView):
    def _inspection(self, request, inspection_id):
        return get_object_or_404(
            InspectionAccessPolicy.queryset_for(request.user), pk=inspection_id
        )

    def get(self, request, inspection_id):
        inspection = self._inspection(request, inspection_id)
        materials = inspection.materials.order_by("created_at", "id")
        return Response(MaterialSerializer(materials, many=True).data)

    def post(self, request, inspection_id):
        inspection = self._inspection(request, inspection_id)
        try:
            if request.data.get("kind") == Material.Kind.TEXT and "file" not in request.FILES:
                material, created = create_text_material(
                    inspection=inspection,
                    actor=request.user,
                    display_name=str(request.data.get("display_name", "文字说明.txt")),
                    text=str(request.data.get("text", "")),
                )
            else:
                upload = request.FILES.get("file")
                if upload is None:
                    raise UploadValidationError("file_required", "请选择要上传的文件。")
                material, created = create_uploaded_material(
                    inspection=inspection, actor=request.user, upload=upload
                )
        except UploadValidationError as exc:
            raise ApiError(code=exc.code, message=exc.message) from exc
        payload = dict(MaterialSerializer(material).data)
        payload["deduplicated"] = not created
        return Response(
            payload,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MaterialDetailView(APIView):
    def delete(self, request, material_id):
        material = get_object_or_404(material_queryset_for(request.user), pk=material_id)
        if request.data.get("confirm") is not True:
            raise ApiError(code="confirmation_required", message="删除前必须二次确认。")
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            raise ApiError(code="deletion_reason_required", message="请填写删除原因。")
        delete_material(material=material, actor=request.user, reason=reason)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _content_disposition(display_name: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", display_name) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(display_name)}"


class MaterialDownloadView(APIView):
    def get(self, request, material_id):
        material = get_object_or_404(material_queryset_for(request.user), pk=material_id)
        try:
            path = controlled_path(material)
            stream = path.open("rb")
        except (OSError, UploadValidationError) as exc:
            raise ApiError(
                code="file_unavailable",
                message="文件暂时不可用。",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        response = FileResponse(stream, content_type=material.mime_type)
        response["Content-Disposition"] = _content_disposition(material.display_name)
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MaterialPreviewView(APIView):
    def get(self, request, material_id):
        material = get_object_or_404(material_queryset_for(request.user), pk=material_id)
        if material.kind != Material.Kind.IMAGE:
            raise ApiError(
                code="preview_unavailable",
                message="该材料没有可用的图片预览。",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        try:
            path = controlled_path(material)
            stream = path.open("rb")
        except (OSError, UploadValidationError) as exc:
            raise ApiError(
                code="file_unavailable",
                message="文件暂时不可用。",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        response = FileResponse(stream, content_type=material.mime_type)
        response["Content-Disposition"] = _content_disposition(material.display_name).replace(
            "attachment;", "inline;", 1
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class _MaterialDerivativeFileView(APIView):
    as_attachment = False

    def get(self, request, derivative_id):
        derivative = get_object_or_404(derivative_queryset_for(request.user), pk=derivative_id)
        try:
            path = controlled_storage_key(derivative.storage_key)
            stream = path.open("rb")
        except (OSError, UploadValidationError) as exc:
            raise ApiError(
                code="file_unavailable",
                message="文件暂时不可用。",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        extension = path.suffix if len(path.suffix) <= 16 else ""
        filename = f"{derivative.kind}-{derivative.id}{extension}"
        response = FileResponse(stream, content_type=derivative.mime_type)
        response["Content-Disposition"] = _content_disposition(filename).replace(
            "attachment;",
            "attachment;" if self.as_attachment else "inline;",
            1,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MaterialDerivativePreviewView(_MaterialDerivativeFileView):
    as_attachment = False


class MaterialDerivativeDownloadView(_MaterialDerivativeFileView):
    as_attachment = True


def _range_iterator(path: Path, start: int, length: int, chunk_size: int = 64 * 1024):
    with path.open("rb") as stream:
        stream.seek(start)
        remaining = length
        while remaining:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


class MaterialPlayView(APIView):
    RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

    def get(self, request, material_id):
        material = get_object_or_404(material_queryset_for(request.user), pk=material_id)
        derivative = (
            material.derivatives.filter(kind="playback").order_by("-created_at", "-id").first()
        )
        try:
            if derivative is not None:
                candidate = controlled_storage_key(derivative.storage_key)
                path = candidate if candidate.is_file() else controlled_path(material)
            else:
                path = controlled_path(material)
            total = path.stat().st_size
        except (OSError, UploadValidationError) as exc:
            raise ApiError(
                code="file_unavailable",
                message="文件暂时不可用。",
                status_code=status.HTTP_404_NOT_FOUND,
            ) from exc
        range_header = request.headers.get("Range")
        if not range_header:
            content_type = (
                derivative.mime_type if derivative and path == candidate else material.mime_type
            )
            response = FileResponse(path.open("rb"), content_type=content_type)
            response["Content-Length"] = total
            response["Accept-Ranges"] = "bytes"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        match = self.RANGE_RE.fullmatch(range_header.strip())
        if not match:
            return Response(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
        raw_start, raw_end = match.groups()
        try:
            if not raw_start:
                suffix = int(raw_end or 0)
                start = max(total - suffix, 0)
                end = total - 1
            else:
                start = int(raw_start)
                end = min(int(raw_end) if raw_end else total - 1, total - 1)
        except ValueError:
            return Response(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
        if start < 0 or start >= total or end < start:
            response = Response(status=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
            response["Content-Range"] = f"bytes */{total}"
            return response
        length = end - start + 1
        response = StreamingHttpResponse(
            _range_iterator(path, start, length),
            status=status.HTTP_206_PARTIAL_CONTENT,
            content_type=(
                derivative.mime_type if derivative and path == candidate else material.mime_type
            ),
        )
        response["Content-Length"] = length
        response["Content-Range"] = f"bytes {start}-{end}/{total}"
        response["Accept-Ranges"] = "bytes"
        response["X-Content-Type-Options"] = "nosniff"
        return response
