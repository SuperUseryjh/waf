#!/bin/bash

# 如果任何命令失败，则立即退出
set -e

echo "开始部署 WAF 系统..."

# --- 检查 Docker 是否安装 ---
if ! command -v docker &> /dev/null
then
    echo "错误：Docker 未安装。WAF 系统需要 Docker 才能运行。"
    read -p "您希望自动安装 Docker 吗？(y/N): " install_docker_choice
    if [[ "$install_docker_choice" =~ ^[Yy]$ ]]
    then
        echo "正在安装 Docker... 这可能需要您的 sudo 密码。"
        sudo apt-get update
        sudo apt-get install -y docker.io
        if ! command -v docker &> /dev/null
        then
            echo "错误：Docker 安装失败。请手动检查并安装 Docker。"
            exit 1
        fi
        echo "Docker 安装成功。"
        echo "您可能需要将当前用户添加到 docker 组以避免每次运行 docker 命令时都使用 sudo。"
        echo "  sudo usermod -aG docker $USER"
        echo "然后注销并重新登录以使更改生效。"
        # 暂时不退出，让用户决定是否立即重新登录或继续
    else
        echo "取消部署。请先安装 Docker。"
        exit 1
    fi
fi

echo "Docker 已安装。"

# --- 检查 Docker Compose 是否安装 ---
if ! command -v docker-compose &> /dev/null
then
    echo "错误：Docker Compose 未安装。WAF 系统需要 Docker Compose 才能运行。"
    read -p "您希望自动安装 Docker Compose 吗？(y/N): " install_compose_choice
    if [[ "$install_compose_choice" =~ ^[Yy]$ ]]
    then
        echo "正在安装 Docker Compose... 这可能需要您的 sudo 密码。"
        # 推荐使用官方脚本安装 Docker Compose v2
        sudo curl -L "https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
        if ! command -v docker-compose &> /dev/null
        then
            echo "错误：Docker Compose 安装失败。请手动检查并安装 Docker Compose。"
            exit 1
        fi
        echo "Docker Compose 安装成功。"
    else
        echo "取消部署。请先安装 Docker Compose。"
        exit 1
    fi
fi

echo "Docker Compose 已安装。"

# --- 部署 WAF 系统 ---
echo "停止并移除任何现有的 WAF 服务..."
docker-compose down || true # 即使没有服务运行，也允许此命令失败

echo "构建 Docker 镜像并启动服务..."
docker-compose up --build -d

echo "WAF 系统部署成功！"
echo "您可以通过 http://localhost:5000 访问 WAF 监控面板。"
echo "WAF 正在保护通过 http://localhost (或您配置的后端) 访问的服务。"
