import { expect, test } from "vitest";
import { requestError } from "./requestError";
test("shows the backend guidance without HTTP wrappers", async () => {
  expect((await requestError({ status: 400, text: async () => JSON.stringify({ detail: "请前往设置填写表格密钥。" }) })).message).toBe("请前往设置填写表格密钥。");
});
test("hides non-JSON upstream pages", async () => {
  expect((await requestError({ status: 502, text: async () => "<html>proxy error</html>" })).message).toBe("服务暂时不可用，请稍后重试。");
});
