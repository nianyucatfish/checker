/** Extract the user-facing message returned by the local backend. */
export async function requestError(response: Pick<Response, "status" | "text">): Promise<Error> {
  try {
    const payload = JSON.parse(await response.text());
    const message = typeof payload.detail === "string" ? payload.detail :
      typeof payload.detail?.message === "string" ? payload.detail.message :
      typeof payload.error === "string" ? payload.error : null;
    if (message) return new Error(message);
  } catch { /* A proxy may return HTML instead of the API's JSON response. */ }
  return new Error(response.status === 422 ? "填写内容有误，请检查后重试。" :
    response.status >= 500 ? "服务暂时不可用，请稍后重试。" : "操作未完成，请检查设置后重试。");
}
