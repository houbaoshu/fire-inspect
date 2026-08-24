import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { api, createIdempotencyKey, getErrorMessage } from "../api/client";
import type { Inspection } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  INSPECTION_TYPES,
  shanghaiDateTimeInputToIso,
  toShanghaiDateTimeInput,
} from "../utils/format";

interface InspectionFormState {
  unit_name: string;
  unit_address: string;
  responsible_person: string;
  contact_name: string;
  contact_phone: string;
  inspection_at: string;
  inspection_type: string;
  inspector_names: string;
  general_notes: string;
}

function currentLocalDateTime() {
  return toShanghaiDateTimeInput(new Date());
}

export function InspectionFormPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState<InspectionFormState>({
    unit_name: "",
    unit_address: "",
    responsible_person: "",
    contact_name: "",
    contact_phone: "",
    inspection_at: currentLocalDateTime(),
    inspection_type: "other",
    inspector_names: user?.display_name || user?.username || "",
    general_notes: "",
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.post<Inspection | { inspection: Inspection }>(
        "/inspections/",
        {
          unit_name: form.unit_name.trim(),
          unit_address: form.unit_address.trim(),
          responsible_person: form.responsible_person.trim(),
          contact_name: form.contact_name.trim(),
          contact_phone: form.contact_phone.trim(),
          inspection_at: shanghaiDateTimeInputToIso(form.inspection_at),
          inspection_type: form.inspection_type,
          general_notes: form.general_notes.trim(),
          personnel: form.inspector_names
            .split(/[、,，]/)
            .map((name) => name.trim())
            .filter(Boolean)
            .map((name, sequence) => ({ name, role: "inspector", sequence })),
        },
        { idempotencyKey: createIdempotencyKey("create-inspection") },
      ),
    onSuccess: (response) => {
      const inspection = "inspection" in response ? response.inspection : response;
      navigate(`/inspections/${inspection.id}/materials`, { replace: true });
    },
  });

  const update = (key: keyof InspectionFormState, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!form.unit_name.trim() || !form.unit_address.trim() || !form.inspection_at || !form.inspector_names.trim()) return;
    createMutation.mutate();
  };

  return (
    <div className="page narrow-page">
      <header className="page-header">
        <div>
          <Link className="back-link" to="/inspections">← 返回检查列表</Link>
          <p className="eyebrow">新建检查</p>
          <h1>建立检查档案</h1>
          <p>先填写检查级共享信息。后续三类文书会始终使用这里的当前值。</p>
        </div>
      </header>

      <form className="panel form-panel" onSubmit={submit}>
        <div className="form-section-heading">
          <span>01</span>
          <div><h2>被检查单位</h2><p>这些字段会同步用于三类文书。</p></div>
        </div>
        <div className="form-grid two-columns">
          <label className="field field-wide">
            <span>单位（场所）名称 <b aria-hidden="true">*</b></span>
            <input value={form.unit_name} onChange={(event) => update("unit_name", event.target.value)} maxLength={200} required />
          </label>
          <label className="field field-wide">
            <span>地址 <b aria-hidden="true">*</b></span>
            <input value={form.unit_address} onChange={(event) => update("unit_address", event.target.value)} maxLength={300} required />
          </label>
          <label className="field">
            <span>法定代表人 / 主要负责人</span>
            <input value={form.responsible_person} onChange={(event) => update("responsible_person", event.target.value)} maxLength={100} />
          </label>
          <label className="field">
            <span>联系人</span>
            <input value={form.contact_name} onChange={(event) => update("contact_name", event.target.value)} maxLength={100} />
          </label>
          <label className="field">
            <span>联系电话</span>
            <input inputMode="tel" value={form.contact_phone} onChange={(event) => update("contact_phone", event.target.value)} maxLength={30} pattern="[0-9+()（）\-\s]{5,30}" />
          </label>
        </div>

        <div className="form-section-heading">
          <span>02</span>
          <div><h2>检查安排</h2><p>时间在界面按 Asia/Shanghai 显示。</p></div>
        </div>
        <div className="form-grid two-columns">
          <label className="field">
            <span>检查时间 <b aria-hidden="true">*</b></span>
            <input type="datetime-local" value={form.inspection_at} onChange={(event) => update("inspection_at", event.target.value)} required />
          </label>
          <label className="field">
            <span>检查人员 <b aria-hidden="true">*</b></span>
            <input value={form.inspector_names} onChange={(event) => update("inspector_names", event.target.value)} placeholder="多人请用顿号分隔" maxLength={300} required />
          </label>
          <fieldset className="field field-wide inspection-type-fieldset">
            <legend>检查类型 <b aria-hidden="true">*</b></legend>
            <div className="radio-card-grid">
              {INSPECTION_TYPES.map(([value, label]) => (
                <label className={form.inspection_type === value ? "radio-card selected" : "radio-card"} key={value}>
                  <input type="radio" name="inspection_type" value={value} checked={form.inspection_type === value} onChange={(event) => update("inspection_type", event.target.value)} />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <label className="field field-wide">
            <span>备注</span>
            <textarea value={form.general_notes} onChange={(event) => update("general_notes", event.target.value)} rows={4} maxLength={2000} placeholder="可补充检查背景、现场联系人安排等信息" />
          </label>
        </div>

        {createMutation.isError ? <div className="notice notice-error" role="alert">{getErrorMessage(createMutation.error)}</div> : null}
        <div className="form-actions">
          <Link className="button button-ghost" to="/inspections">取消</Link>
          <button className="button button-primary" type="submit" disabled={createMutation.isPending}>
            {createMutation.isPending ? "正在创建…" : "创建并进入工作台"}
          </button>
        </div>
      </form>
    </div>
  );
}
