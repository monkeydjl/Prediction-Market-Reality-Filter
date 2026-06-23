import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { getOperatorApiKey } from "@/lib/api";
import { OperatorKeyControl } from "./operator-key-control";

describe("OperatorKeyControl", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("exposes accessible controls and saves a trimmed operator key", async () => {
    render(<OperatorKeyControl />);

    await userEvent.click(await screen.findByRole("button", { name: "配置写接口 API key" }));
    const input = screen.getByLabelText("写接口 API key");

    await userEvent.type(input, "  secret-key  ");
    await userEvent.click(screen.getByRole("button", { name: "保存写接口 API key" }));

    expect(getOperatorApiKey()).toBe("secret-key");
    expect(await screen.findByRole("button", { name: "编辑写接口 API key" })).toHaveTextContent("已授权");
  });

  it("restores the stored key when editing is cancelled", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    render(<OperatorKeyControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "编辑写接口 API key" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "编辑写接口 API key" }));
    await userEvent.clear(screen.getByLabelText("写接口 API key"));
    await userEvent.type(screen.getByLabelText("写接口 API key"), "draft-key");
    await userEvent.click(screen.getByRole("button", { name: "取消编辑写接口 API key" }));

    expect(getOperatorApiKey()).toBe("stored-key");
    expect(screen.getByRole("button", { name: "编辑写接口 API key" })).toHaveTextContent("已授权");
  });

  it("clears authorization when an empty key is saved", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    render(<OperatorKeyControl />);

    await waitFor(() => expect(screen.getByRole("button", { name: "编辑写接口 API key" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "编辑写接口 API key" }));
    await userEvent.clear(screen.getByLabelText("写接口 API key"));
    await userEvent.click(screen.getByRole("button", { name: "保存写接口 API key" }));

    expect(getOperatorApiKey()).toBe("");
    expect(await screen.findByRole("button", { name: "配置写接口 API key" })).toHaveTextContent("授权");
  });
});
