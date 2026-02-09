# Search API

基于 FastAPI 构建的搜索服务 API。

## 项目结构

```
search-api/
├── src/
│   └── app/
│       ├── api/            # API 路由层
│       │   └── v1/         # API 版本
│       ├── core/           # 核心配置
│       ├── schemas/        # Pydantic 数据模型
│       ├── services/       # 业务逻辑层
│       └── main.py         # 应用入口
├── pyproject.toml          # 项目配置
└── README.md
```

## 快速开始

### 安装依赖

```bash
uv sync
```

### 运行服务器

**推荐方式（支持外网访问）：**

```bash
uv run run.py
```

或者直接使用 uvicorn 命令：

```bash
uv run uvicorn src.app.main:app --host 0.0.0.0 --port 56808
```

> 注意：部署在服务器上时，请确保：
> 1. 服务器防火墙已开放 56808 端口
> 2. 云服务器的安全组已允许 56808 端口的入站流量

### 访问 API 文档
（注意：为了安全，Swagger UI 默认已禁用，如需开启请修改 `src/app/main.py`）
