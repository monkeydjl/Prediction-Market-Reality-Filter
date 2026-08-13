import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TitleTranslationPanel } from "./title-translation-panel";
import { eventsApi } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    translateEvent: vi.fn(),
  },
}));

describe("TitleTranslationPanel", () => {
  beforeEach(() => {
    vi.mocked(eventsApi.translateEvent).mockReset();
  });

  it("offers a first translation when no Chinese title exists", async () => {
    vi.mocked(eventsApi.translateEvent).mockResolvedValue({
      event_id: "evt-1",
      event_title_zh: "某事件会发生吗？",
      message: "Translated",
    });
    const onTranslated = vi.fn();
    render(
      <TitleTranslationPanel eventId="evt-1" titleZh={null} onTranslated={onTranslated} />,
    );

    expect(screen.getByTestId("title-translation-panel")).toHaveTextContent(
      "该事件还没有中文标题",
    );
    await userEvent.click(screen.getByRole("button", { name: "翻译标题" }));

    // force=false: a missing title never needs the re-translate override.
    expect(eventsApi.translateEvent).toHaveBeenCalledWith("evt-1", false);
    expect(onTranslated).toHaveBeenCalledWith("某事件会发生吗？");
    expect(await screen.findByText("Translated")).toBeInTheDocument();
  });

  it("forces a re-translation when a Chinese title is already present", async () => {
    vi.mocked(eventsApi.translateEvent).mockResolvedValue({
      event_id: "evt-1",
      event_title_zh: "更好的中文标题",
      message: "Translated",
    });
    render(
      <TitleTranslationPanel
        eventId="evt-1"
        titleZh="旧的中文标题"
        onTranslated={vi.fn()}
      />,
    );

    expect(screen.getByTestId("title-translation-panel")).toHaveTextContent("旧的中文标题");
    await userEvent.click(screen.getByRole("button", { name: "重新翻译" }));

    // Without force the backend short-circuits with "Already translated".
    expect(eventsApi.translateEvent).toHaveBeenCalledWith("evt-1", true);
  });

  it("surfaces the write-key rejection instead of a silent no-op", async () => {
    vi.mocked(eventsApi.translateEvent).mockRejectedValue(
      new Error("401 需要写入密钥"),
    );
    const onTranslated = vi.fn();
    render(
      <TitleTranslationPanel eventId="evt-1" titleZh={null} onTranslated={onTranslated} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "翻译标题" }));

    expect(await screen.findByText("401 需要写入密钥")).toBeInTheDocument();
    expect(onTranslated).not.toHaveBeenCalled();
  });
});
