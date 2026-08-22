export function buildDefaultDocumentTitle(
  inspection: { title: string; unit_name?: string | null },
  documentTypeLabel: string,
): string {
  const subject = inspection.unit_name?.trim() || inspection.title.trim()
  return `${subject}-${documentTypeLabel}`
}
