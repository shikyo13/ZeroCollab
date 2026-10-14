import { runtime } from './api';

export type ImportSource = 'claude' | 'codex';
export interface ImportPreview {
  token: string; label: string; title: string; session_id: string;
  cwd: string; cwd_exists: boolean; branch: string; commit: string;
  started: number | null; ended: number | null;
  messages: number; user_messages: number; assistant_messages: number;
  skipped: number; truncated: number; duplicate: boolean;
  sample: { role: string; text: string; time: number | null }[];
}
export interface ImportReceipt {
  project_id: string; project_name: string; new_project: boolean;
  chat_id: string; chat_title: string; messages: number; brief: string;
  cwd: string; cwd_exists: boolean;
}
export function previewImport(source: ImportSource, file: File) {
  const form = new FormData();
  form.append('source', source);
  form.append('file', file);
  return runtime<ImportPreview>('/import/preview', form);
}
export const confirmImport = (body: { token: string; destination: 'new' | 'existing'; name?: string; project_id?: string }) =>
  runtime<ImportReceipt>('/import/confirm', body);
