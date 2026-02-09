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

### 运行开发服务器

```bash
uv run uvicorn src.app.main:app --reload --host 0.0.0.0 --port 8000
```

### 访问 API 文档

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
