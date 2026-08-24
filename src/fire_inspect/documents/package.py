"""Small OOXML package patches that preserve every untouched part verbatim."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from fire_inspect.templates.security import validate_docx

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W_NS, "r": R_NS, "pr": PR_NS}


def rewrite_package_parts(payload: bytes, replacements: dict[str, bytes]) -> bytes:
    source_stream = BytesIO(payload)
    output = BytesIO()
    with ZipFile(source_stream) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        existing = set(source.namelist())
        unknown = set(replacements) - existing
        if unknown:
            raise ValueError("不能替换 DOCX 中不存在的包项")
        for info in source.infolist():
            content = replacements.get(info.filename, source.read(info.filename))
            target.writestr(info, content)
    result = output.getvalue()
    validate_docx(result, filename="generated.docx")
    return result


def strip_external_attached_template(payload: bytes) -> bytes:
    """Remove the trusted source's legacy local .dotx link from a generated copy."""

    settings_name = "word/settings.xml"
    relationships_name = "word/_rels/settings.xml.rels"
    with ZipFile(BytesIO(payload)) as archive:
        if relationships_name not in archive.namelist():
            return payload
        relationships = etree.fromstring(
            archive.read(relationships_name),
            parser=etree.XMLParser(resolve_entities=False, no_network=True),
        )
        removed_ids: set[str] = set()
        for relationship in relationships.xpath(
            ".//pr:Relationship[@TargetMode='External']",
            namespaces=NS,
        ):
            relationship_type = relationship.get("Type", "")
            if relationship_type.endswith("/attachedTemplate"):
                removed_ids.add(relationship.get("Id", ""))
                relationship.getparent().remove(relationship)
        if not removed_ids:
            return payload
        settings = etree.fromstring(
            archive.read(settings_name),
            parser=etree.XMLParser(resolve_entities=False, no_network=True),
        )
        for attached in settings.xpath(".//w:attachedTemplate", namespaces=NS):
            if attached.get(f"{{{R_NS}}}id") in removed_ids:
                attached.getparent().remove(attached)
        replacements = {
            relationships_name: etree.tostring(
                relationships,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            ),
            settings_name: etree.tostring(
                settings,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            ),
        }

    source_stream = BytesIO(payload)
    output = BytesIO()
    with ZipFile(source_stream) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, replacements.get(info.filename, source.read(info.filename)))
    result = output.getvalue()
    validate_docx(result, filename="generated.docx")
    return result


def finalize_interview_page_count(payload: bytes, *, page_count: int) -> bytes:
    """Update every cached NUMPAGES result while retaining the live field code."""

    if page_count < 1:
        raise ValueError("询问笔录页数必须大于零")
    replacements: dict[str, bytes] = {}
    with ZipFile(BytesIO(payload)) as archive:
        footer_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("word/footer") and name.endswith(".xml")
        )
        if not footer_names:
            raise ValueError("询问笔录缺少页脚")
        for name in footer_names:
            root = etree.fromstring(
                archive.read(name),
                parser=etree.XMLParser(resolve_entities=False, no_network=True),
            )
            fields = root.xpath(".//w:fldSimple", namespaces=NS)
            numpages_fields = [
                field for field in fields if "NUMPAGES" in field.get(f"{{{W_NS}}}instr", "").upper()
            ]
            if not numpages_fields:
                raise ValueError("询问笔录页脚缺少 NUMPAGES 域")
            for field in numpages_fields:
                text_nodes = field.xpath(".//w:t", namespaces=NS)
                if not text_nodes:
                    raise ValueError("询问笔录 NUMPAGES 域缺少缓存值")
                text_nodes[0].text = str(page_count)
            replacements[name] = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
    return rewrite_package_parts(payload, replacements)
