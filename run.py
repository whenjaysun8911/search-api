
import uvicorn
import os

if __name__ == "__main__":
    # 获取端口，默认为 56808
    port = int(os.getenv("PORT", 56808))
    
    # 获取是否开启热重载
    reload = os.getenv("DEBUG", "false").lower() == "true"
    
    # 启动 uvicorn
    # host="0.0.0.0" 允许外部访问
    uvicorn.run(
        "src.app.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        proxy_headers=True, # 信任代理头，这对于在 Nginx 等反向代理后运行很重要
        forwarded_allow_ips="*" # 允许所有 IP 的转发头
    )
