# v2/ · 重构前快照（历史参考，请勿修改）

这里是 **v2 重构开始之前** 的 FreeLake 实现，取自提交 `5dbccbd`
（"fix(security/perf): 页面级鉴权、Cookie 密钥 fail-safe、SQL 结果脱敏与渲染缓存"）的代码树。

- 用途：对照阅读、回滚参考、审计差异。**不要在这里改代码**。
- 当前在用的实现位于仓库根目录（`main.py`、`api.py`、`pages/`、`freelake/`…）。
- 更早的一代实现仍保留在 `v1/`。
- 快照不含运行期文件（`data.db`、`attachments/`、`avatars/`、`.streamlit/secrets.toml`）；
  直接 `import api` 会在本目录生成空 `data.db`，只想读代码就不必运行。

重构的设计与差异见 `docs/v2-refactor-plan.md`。
