import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import {
  clearOperatorCredentials,
  getOperatorApiKey,
  getOperatorId,
  requestOpenOperatorKey,
} from "@/lib/operator-credentials";
import { OperatorKeyControl } from "./operator-key-control";

describe("OperatorKeyControl", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearOperatorCredentials();
    window.location.hash = "";
  });

  it("saves a trimmed operator key and operator id", async () => {
    render(<OperatorKeyControl />);

    await userEvent.click(
      await screen.findByLabelText("配置写接口 API key"),
    );
    await userEvent.type(screen.getByPlaceholderText("API key"), "  secret-key  ");
    await userEvent.type(screen.getByLabelText("Operator"), "  alice  ");
    await userEvent.click(screen.getByLabelText("保存写接口 API key"));

    expect(getOperatorApiKey()).toBe("secret-key");
    expect(getOperatorId()).toBe("alice");
    expect(
      await screen.findByLabelText("编辑写接口 API key"),
    ).toBeInTheDocument();
  });

  it("restores stored values when editing is cancelled", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    window.sessionStorage.setItem("pmrf.operatorId", "stored-operator");
    render(<OperatorKeyControl />);

    await userEvent.click(
      await screen.findByLabelText("编辑写接口 API key"),
    );
    await userEvent.clear(screen.getByPlaceholderText("API key"));
    await userEvent.type(screen.getByPlaceholderText("API key"), "draft-key");
    await userEvent.clear(screen.getByLabelText("Operator"));
    await userEvent.type(screen.getByLabelText("Operator"), "draft-operator");
    await userEvent.click(screen.getByLabelText("取消编辑写接口 API key"));

    expect(getOperatorApiKey()).toBe("stored-key");
    expect(getOperatorId()).toBe("stored-operator");
  });

  it("clears authorization and operator id when empty values are saved", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    window.sessionStorage.setItem("pmrf.operatorId", "stored-operator");
    render(<OperatorKeyControl />);

    await userEvent.click(
      await screen.findByLabelText("编辑写接口 API key"),
    );
    await userEvent.clear(screen.getByPlaceholderText("API key"));
    await userEvent.clear(screen.getByLabelText("Operator"));
    await userEvent.click(screen.getByLabelText("保存写接口 API key"));

    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
  });

  it("clears credentials via clear button", async () => {
    window.sessionStorage.setItem("pmrf.operatorApiKey", "stored-key");
    window.sessionStorage.setItem("pmrf.operatorId", "ops");
    render(<OperatorKeyControl />);

    await userEvent.click(await screen.findByLabelText("清除写接口授权"));
    expect(getOperatorApiKey()).toBe("");
    expect(getOperatorId()).toBe("");
    expect(
      await screen.findByLabelText("配置写接口 API key"),
    ).toBeInTheDocument();
  });

  it("opens edit form on requestOpenOperatorKey event", async () => {
    render(<OperatorKeyControl />);
    expect(
      await screen.findByLabelText("配置写接口 API key"),
    ).toBeInTheDocument();
    requestOpenOperatorKey({ setHash: false });
    expect(await screen.findByPlaceholderText("API key")).toBeInTheDocument();
  });
});
