const de = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const eur0 = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const eur4 = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 4 });

export const num = (x: number) => de.format(x);
export const eur = (x: number) => (Math.abs(x) < 1 && x !== 0 ? eur4.format(x) : eur0.format(x));
export const mean = (x: number) => new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(x);
export const pct = (x: number) => `${de.format(x)} %`;

export function dims(t: number, w: number, l: number | null): string {
  return `${num(t)} × ${num(w)} × ${l ? num(l) : "C"}`;
}

export const fieldName: Record<string, string> = {
  grade: "Grade", thickness_mm: "Thickness", width_mm: "Width", length_mm: "Length", quantity_t: "Quantity",
  max_price_eur_t: "Target price", form: "Coil or sheets", finish: "Finish", coating: "Coating", surface: "Surface",
  certificate: "Certificate", delivery_country: "Delivery country", delivery_by: "Delivery date", lines: "Product lines",
  model: "Model", "policy.upgrades": "Higher grade allowed", "policy.secondary": "Second choice allowed",
  "policy.certificate": "Certificate", "policy.origins": "Excluded origins", "delivery.place": "Delivery place",
  "delivery.date": "Delivery date",
};

export function shortSender(s: string): string {
  return s.replace(/<.*>/, "").trim();
}
