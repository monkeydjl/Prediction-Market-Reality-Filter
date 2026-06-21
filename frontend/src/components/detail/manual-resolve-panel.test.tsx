import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ManualResolvePanel } from "./manual-resolve-panel";
import { eventsApi } from "@/lib/api";
import type { EventRecord, TrackedEntry } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    resolveManual: vi.fn(),
  },
}));

const record: EventRecord = {
  event_id: "evt-1",
  event_title: "Will the event happen?",
};

describe("ManualResolvePanel", () => {
  beforeEach(() => {
    vi.mocked(eventsApi.resolveManual).mockReset();
  });

  it("validates actual outcome range", async () => {
    render(<ManualResolvePanel record={record} onResolved={vi.fn()} />);

    // fireEvent.change bypasses HTML5 constraint validation (max=100) in jsdom.
    fireEvent.change(screen.getByLabelText("实际结果（0–100）"), {
      target: { value: "120" },
    });
    // fireEvent.submit on the form bypasses HTML5 constraint validation that
    // would otherwise prevent the submit event from firing for out-of-range
    // number inputs.
    const form = screen.getByRole("button", { name: "确认结算" }).closest("form")!;
    fireEvent.submit(form);

    expect(screen.getByText("实际结果必须在 0 到 100 之间")).toBeInTheDocument();
    expect(eventsApi.resolveManual).not.toHaveBeenCalled();
  });

  it("requires confirmation before submitting a valid manual resolution", async () => {
    const onResolved = vi.fn();
    const entry: TrackedEntry = { event_id: "evt-1", record };
    vi.mocked(eventsApi.resolveManual).mockResolvedValue(entry);
    render(<ManualResolvePanel record={record} onResolved={onResolved} />);

    await userEvent.type(screen.getByLabelText("实际结果（0–100）"), "100");
    await userEvent.clear(screen.getByLabelText("置信度（0–1）"));
    await userEvent.type(screen.getByLabelText("置信度（0–1）"), "0.9");
    await userEvent.type(screen.getByLabelText("备注"), "official result");
    await userEvent.click(screen.getByRole("button", { name: "确认结算" }));

    expect(eventsApi.resolveManual).not.toHaveBeenCalled();
    expect(screen.getByText("再次确认后写入：结果 100%，置信度 0.9。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "写入结算" }));

    expect(eventsApi.resolveManual).toHaveBeenCalledWith("evt-1", {
      actual_outcome: 100,
      confidence: 0.9,
      notes: "official result",
    });
    expect(onResolved).toHaveBeenCalledWith(entry);
  });
});
