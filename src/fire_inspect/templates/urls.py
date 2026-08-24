from django.urls import re_path

from .views import (
    TemplateDetailView,
    TemplateListCreateView,
    TemplateMappingConfirmView,
    TemplateMappingReviseView,
    TemplateRecognizeView,
    TemplateRegionCreateView,
    TemplateRegionDetailView,
    TemplateVersionCreateView,
)

UUID = r"[0-9a-fA-F-]{36}"

urlpatterns = [
    re_path(r"^templates/?$", TemplateListCreateView.as_view(), name="template-list"),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/?$",
        TemplateDetailView.as_view(),
        name="template-detail",
    ),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/versions/?$",
        TemplateVersionCreateView.as_view(),
        name="template-version-create",
    ),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/recognize/?$",
        TemplateRecognizeView.as_view(),
        name="template-recognize",
    ),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/mapping/confirm/?$",
        TemplateMappingConfirmView.as_view(),
        name="template-mapping-confirm",
    ),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/mapping/revise/?$",
        TemplateMappingReviseView.as_view(),
        name="template-mapping-revise",
    ),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/mapping/regions/?$",
        TemplateRegionCreateView.as_view(),
        name="template-region-create",
    ),
    re_path(
        rf"^templates/(?P<template_id>{UUID})/mapping/regions/(?P<region_id>{UUID})/?$",
        TemplateRegionDetailView.as_view(),
        name="template-region-detail",
    ),
]
