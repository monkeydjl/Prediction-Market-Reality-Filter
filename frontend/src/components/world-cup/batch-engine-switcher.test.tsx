import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BatchEngineSwitcher } from "./batch-engine-switcher";

describe("BatchEngineSwitcher", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("offers the integrated engine as a batch option", () => {
    render(<BatchEngineSwitcher />);

    expect(screen.getByText("一键集成引擎")).toBeInTheDocument();
  });

  it("sends the operator key when opening the batch switch stream", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "secret");
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: complete\ndata: {"status":"ok","total":0,"succeeded":0,"failed":0,"skipped":0}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.fn(async () => ({
      ok: true,
      body,
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<BatchEngineSwitcher />);
    fireEvent.click(screen.getAllByRole("button")[0]);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/world-cup/predictions/batch-switch-engine-stream?"),
        expect.objectContaining({
          headers: expect.objectContaining({ "X-API-Key": "secret" }),
          cache: "no-store",
        }),
      );
    });
  });
});
