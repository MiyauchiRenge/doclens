# rpms/

## 作用

目标机若**没有预装 Docker**，`deploy/install.sh` 会尝试从本目录离线安装 RPM。

## 怎么准备

### 方式一：构建机自动抓取（推荐）

在联网构建机执行：

```bash
./build/build-bundle.sh --with-rpms
```

这要求构建机已配置 docker-ce 源（或 openEuler 自带的 docker 源），
脚本会调用 `dnf download --resolve` 把依赖一并抓下来。

### 方式二：手动准备

把下列包（及依赖）拷到本目录：

- `docker-ce` / `docker-ce-cli`（或 openEuler 源里的 `docker` / `docker-engine`）
- `containerd.io`
- `docker-compose-plugin`（或 `docker-compose`）
- 必要的依赖：`container-selinux`、`libcgroup`、`iptables` 等

## 注意

- **openEuler 各版本、麒麟 V10 的包名与依赖并不完全一致**，构建机抓的包不一定能装到另一台机器上。
  最稳妥的做法是：**在目标机同版本的系统上抓包**（可用同版本虚拟机）。
- 若目标机已有可用的内网 yum/dnf 源，直接 `dnf install -y docker docker-compose-plugin` 更省事，
  本目录可以留空。
- 安装脚本会优先用 `dnf install`，失败才回退到 `rpm -Uvh --nodeps`；
  无依赖的裸 `rpm` 安装可能因缺依赖失败，届时请检查输出。

## 空目录占位

本目录在无包时保留一个 `.keep` 占位文件，不影响安装流程。
