import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const ingestMocks = vi.hoisted(() => ({
  status: vi.fn(),
  sourceStatus: vi.fn(),
  facts: vi.fn(),
  runConfigured: vi.fn(),
  runPayload: vi.fn(),
  resolve: vi.fn(),
}));

vi.mock("@/lib/world-cup/ingest-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/world-cup/ingest-api")>()),
  ingestApi: ingestMocks,
}));

import { IngestConsole } from "./ingest-console";

describe("IngestConsole", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ingestMocks.status.mockResolvedValue({ status: "ok", count: 42 });
  });

  it("previews a configured source without a second confirmation", async () => {
    ingestMocks.runConfigured.mockResolvedValue({ converted_fact_count: 7 });
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("source-api-football-preview"));

    expect(ingestMocks.runConfigured).toHaveBeenCalledWith(
      "data/bundle/api-football",
      "preview",
      false,
    );
    expect(screen.queryByTestId("ingest-confirm")).not.toBeInTheDocument();
    expect(await screen.findByText(/可转换 7 条事实/)).toBeInTheDocument();
  });

  it("gates a configured-source import behind a confirmation", async () => {
    ingestMocks.runConfigured.mockResolvedValue({ imported: 30, error_count: 0 });
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("source-bundle-url-import"));
    expect(ingestMocks.runConfigured).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("ingest-confirm-yes"));

    expect(ingestMocks.runConfigured).toHaveBeenCalledWith("data/bundle/url", "import", false);
    expect(await screen.findByText(/已导入 30/)).toBeInTheDocument();
  });

  it("warns that replace mode is destructive and passes the flag through", async () => {
    ingestMocks.runConfigured.mockResolvedValue({ imported: 5, replaced: 12 });
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("replace-toggle"));
    await userEvent.click(screen.getByTestId("source-data-file-import"));

    expect(screen.getByTestId("ingest-confirm")).toHaveTextContent("先清空");
    await userEvent.click(screen.getByTestId("ingest-confirm-yes"));

    expect(ingestMocks.runConfigured).toHaveBeenCalledWith("data/source", "import", true);
  });

  it("cancelling the confirmation leaves the fact store untouched", async () => {
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("source-sportmonks-import"));
    await userEvent.click(screen.getByTestId("ingest-confirm-no"));

    expect(screen.queryByTestId("ingest-confirm")).not.toBeInTheDocument();
    expect(ingestMocks.runConfigured).not.toHaveBeenCalled();
  });

  it("rejects malformed JSON before spending a request", async () => {
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("payload-input"));
    await userEvent.paste("{not json");
    await userEvent.click(screen.getByTestId("payload-preview"));

    expect(await screen.findByTestId("ingest-error")).toHaveTextContent("JSON 解析失败");
    expect(ingestMocks.runPayload).not.toHaveBeenCalled();
    expect(screen.queryByTestId("ingest-confirm")).not.toBeInTheDocument();
  });

  it("sends a parsed payload to the selected kind's route", async () => {
    ingestMocks.runPayload.mockResolvedValue({ converted_fact_count: 3 });
    render(<IngestConsole />);

    await userEvent.selectOptions(screen.getByTestId("payload-kind"), "standings");
    await userEvent.click(screen.getByTestId("payload-input"));
    await userEvent.paste('{"standings": []}');
    await userEvent.click(screen.getByTestId("payload-preview"));

    expect(ingestMocks.runPayload).toHaveBeenCalledWith(
      "standings",
      "preview",
      { standings: [] },
      false,
    );
  });

  it("disables preview for the import-only facts payload", async () => {
    render(<IngestConsole />);

    await userEvent.selectOptions(screen.getByTestId("payload-kind"), "facts");

    expect(screen.getByTestId("payload-preview")).toBeDisabled();
    expect(screen.getByTestId("payload-import")).toBeEnabled();
  });

  it("runs a dry-run resolve directly but confirms a real one", async () => {
    ingestMocks.resolve.mockResolvedValue({ dry_run: true, count: 4 });
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("resolve-button"));
    expect(ingestMocks.resolve).toHaveBeenCalledWith(true, 200);
    expect(screen.queryByTestId("ingest-confirm")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("resolve-dry-run"));
    await userEvent.click(screen.getByTestId("resolve-button"));
    expect(ingestMocks.resolve).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByTestId("ingest-confirm-yes"));
    expect(ingestMocks.resolve).toHaveBeenLastCalledWith(false, 200);
  });

  it("locks the mode toggles while a confirmation is open", async () => {
    ingestMocks.runConfigured.mockResolvedValue({ imported: 5, replaced: 12 });
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("replace-toggle"));
    await userEvent.click(screen.getByTestId("source-data-file-import"));

    // The queued execute() already captured replace=true, so letting the
    // operator untick it here would hide the "先清空" warning while the
    // destructive replace still ran.
    expect(screen.getByTestId("replace-toggle")).toBeDisabled();
    expect(screen.getByTestId("resolve-dry-run")).toBeDisabled();

    await userEvent.click(screen.getByTestId("ingest-confirm-yes"));

    expect(ingestMocks.runConfigured).toHaveBeenCalledWith("data/source", "import", true);
    await waitFor(() => expect(screen.getByTestId("replace-toggle")).toBeEnabled());
  });

  it("explains an empty fact query instead of rendering a blank block", async () => {
    ingestMocks.facts.mockResolvedValue({ count: 0, facts: [] });
    render(<IngestConsole />);

    await userEvent.type(screen.getByTestId("fact-kind"), "match_result");
    await userEvent.click(screen.getByTestId("facts-button"));

    expect(ingestMocks.facts).toHaveBeenCalledWith({ kind: "match_result", team: "" });
    expect(await screen.findByTestId("facts-result")).toHaveTextContent("暂无事实");
  });

  it("surfaces a failed action without leaving the console busy", async () => {
    ingestMocks.sourceStatus.mockRejectedValue(new Error("需要有效的操作员 API Key"));
    render(<IngestConsole />);

    await userEvent.click(screen.getByTestId("source-status-button"));

    expect(await screen.findByTestId("ingest-error")).toHaveTextContent(
      "需要有效的操作员 API Key",
    );
    await waitFor(() => expect(screen.getByTestId("facts-button")).toBeEnabled());
  });

  it("flags the API-Football validate-before-import requirement", async () => {
    render(<IngestConsole />);

    expect(screen.getByTestId("source-row-api-football")).toHaveTextContent("409");
    expect(screen.getByTestId("source-api-football-validate")).toBeInTheDocument();
  });
});
