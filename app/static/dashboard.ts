/**
 * Клиент витрины радара. Сборка не нужна: браузер грузит dashboard.js.
 * Этот файл — типы рядом, чтобы править логику в одном стиле с TypeScript.
 */
export type LotStatus = "new" | "watching" | "take" | "reject" | "done";

export interface LotRow {
  id: number;
  subject: string;
  amount_text: string;
  date: string;
  customer_name: string;
  winner_name: string;
  phone: string;
  profile_labels: string[];
  status: LotStatus;
  status_label: string;
  hot: boolean;
}
