import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { router as appRouter } from "./app/router";
import { pieces } from "./features/desk/highlight";

const demo = JSON.parse(readFileSync(`${process.cwd()}/public/demo.json`, "utf8"));

function mount(path = "/", response: () => Promise<Response> = async () => new Response(JSON.stringify(demo))) {
  vi.stubGlobal("fetch", vi.fn(response));
  const router = createRouter({ routeTree: appRouter.routeTree, history: createMemoryHistory({ initialEntries: [path] }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", { value: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("highlight", () => {
  it("marks quoted evidence and keeps the rest of the text", () => {
    const out = pieces("wir benötigen 20 t S235JR, 3,0 x 1500 x C.", [{ key: "1.grade", quote: "S235JR" }, { key: "1.thickness_mm", quote: "3,0 x 1500 x C" }]);
    expect(out.map((p) => p.text).join("")).toBe("wir benötigen 20 t S235JR, 3,0 x 1500 x C.");
    expect(out.filter((p) => p.key).map((p) => p.key)).toEqual(["1.grade", "1.thickness_mm"]);
  });
  it("skips quotes that are not in the body", () => {
    expect(pieces("abc", [{ key: "x", quote: "zzz" }])).toEqual([{ text: "abc" }]);
  });
});

describe("desk", () => {
  it("shows a loading state, then the inbox and an enquiry needing a decision", async () => {
    const open = demo.rfqs.find((r: any) => r.result?.extraction?.review?.length);
    mount(`/?rfq=${open.message_id}`, () => new Promise((ok) => setTimeout(() => ok(new Response(JSON.stringify(demo))), 80)));
    expect(await screen.findByRole("status", { name: "Loading enquiries" })).toBeInTheDocument();
    const inbox = await screen.findByRole("navigation", { name: "Enquiries" });
    expect(within(inbox).getAllByRole("button").length).toBeGreaterThan(5);
    expect(await screen.findByText("Open points for you")).toBeInTheDocument();
  });

  it("filters the inbox and says so when a filter is empty", async () => {
    mount();
    const inbox = await screen.findByRole("navigation", { name: "Enquiries" });
    fireEvent.click(within(inbox).getByRole("button", { name: /^Not an enquiry\s*\d+$/ }));
    const rows = within(inbox).queryAllByText("Not an enquiry");
    expect(rows.length).toBeGreaterThan(0);
  });

  it("explains a failure and offers a retry", async () => {
    mount("/", async () => { throw new TypeError("offline"); });
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent("could not be reached");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows an empty inbox", async () => {
    mount("/", async () => new Response(JSON.stringify({ ...demo, rfqs: [] })));
    expect(await screen.findByText("The inbox is empty")).toBeInTheDocument();
  });

  it("opens an offer to show every check", async () => {
    mount();
    await screen.findByRole("navigation", { name: "Enquiries" });
    const ready = demo.rfqs.find((r: any) => r.result?.lines?.some((l: any) => l.offers.length));
    fireEvent.click(screen.getAllByText(ready.subject)[0]);
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(ready.subject));
    const tag = (await screen.findAllByRole("button", { expanded: false })).find((b) => b.textContent?.includes("€"))!;
    fireEvent.click(tag);
    expect(await screen.findByText("thickness")).toBeInTheDocument();
  });
});

describe("evaluation and models", () => {
  it("compares the four designs", async () => {
    mount("/evaluation");
    expect((await screen.findAllByText("This platform")).length).toBe(2); // dev and held-out tables
    expect(screen.getAllByText("Offers the buyer would reject")).toHaveLength(2);
    expect(screen.getByText("Held-out months, run once")).toBeInTheDocument();
  });
  it("shows the bake-off and the gate", async () => {
    mount("/models");
    expect(await screen.findByText("The evaluation gate")).toBeInTheDocument();
    expect(screen.getByText("open-weights-8b")).toBeInTheDocument();
  });
});
