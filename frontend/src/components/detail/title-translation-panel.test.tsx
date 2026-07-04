import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TitleTranslationPanel } from "./title-translation-panel";

const api = vi.hoisted(() => ({
  translateEvent: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    eventsApi: api,
  };
});

describe("TitleTranslationPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.translateEvent.mockResolvedValue({
      event_id: "evt-1",
      event_title_zh: "利率会下降吗？",
      message: "Translated",
    });
  });

  it("translates the current event title and returns the updated record", async () => {
    const user = userEvent.setup();
    const onTranslated = vi.fn();
    render(
      <TitleTranslationPanel
        record={{ event_id: "evt-1", event_title: "Will rates fall?" }}
        onTranslated={onTranslated}
      />,
    );

    await user.click(screen.getByRole("button", { name: "翻译标题" }));

    await waitFor(() => {
      expect(api.translateEvent).toHaveBeenCalledWith("evt-1", { force: false });
    });
    expect(onTranslated).toHaveBeenCalledWith(expect.objectContaining({
      event_title_zh: "利率会下降吗？",
    }));
  });
});
