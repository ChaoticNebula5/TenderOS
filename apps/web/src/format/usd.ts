// US dollars, for the one place the app shows them: the cost-budget gate
// (plan item 73). LLM spend is recorded in USD (agent_traces'
// estimated_cost_usd, settings.max_tender_cost_usd), unlike every bid
// amount, which is INR and goes through formatInr. Two decimals always:
// spend is usually cents, and "$0.00" must read as an amount, not a blank.
// A missing or non-finite value is "-", the screens' placeholder.
const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatUsd(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? USD.format(value) : "-";
}
