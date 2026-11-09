# write-novel-live 开发规范

## 项目约定

- Python 3.12 + FastAPI + pydantic，uv 管理依赖；服务固定 :8800
- 每个任务（T1-T9）执行前先出实施计划，用户审核通过后再动手；测试通过后再进入下一任务
- 产品需求以 `docs/产品文档.md` 为准，改动需求先改文档再改代码

## 代码拆分（硬性要求）

1. **单文件 ≤ 400 行**。接近上限就按职责拆分（包/模块），禁止把无关逻辑堆进同一文件
2. **一个文件一个职责**：
   - prompt 文本集中在 `src/writer/prompts.py`，不散落在业务代码里
   - 纯函数（清洗、校验、解析）与 IO/LLM 调用分离 —— 纯函数必须可以直接单测
   - 持久化只出现在 `*_store.py` / `novel_store.py`
3. **新增功能先问"放哪个模块"**：与现有模块职责不符时新建模块，而不是往大文件里追加
4. API 路由层（`api.py`）只做参数校验和转发，业务逻辑在引擎/服务层

## 其他

- 配置：config.json（gitignore）+ config.example.json 模板 + 环境变量覆盖 key
- 单测：核心纯逻辑必须配 pytest（`tests/`），LLM 相关测试用 mock（httpx MockTransport），不烧钱
- 提交：任务验收通过后按用户指示提交
