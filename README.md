# hakios-lafa（CI 构建包）

这个仓库**只做一件事**：在 GitHub 的机器上编出 realme GT8 Pro（lafa / SM8850）的 HakiOS 内核，
产物在 Actions 里下载。本地那台机器不需要 Linux——它只负责 `fastboot`。

```
.github/workflows/build-kernel.yml   CI 定义（ubuntu-22.04，装 theos + 工具链 + SDK，编 kernel + loader）
lafa/                                设备包（设备仓库的全部内容，来自 hakios-lafa/devices/lafa）
```

`lafa/` 会被 workflow 复制进 `hakios/devices/lafa`，所以它和上游的设备仓库是同一个东西：

```
device.conf            18 项 status 全是 missing（诚实起点）
configs/lafa_defconfig  #include common_defconfig / sm8850.config / lafa.config
configs/lafa.config     CONFIG_DEVICE="lafa"，手机驱动全关，CONFIG_FIRMWARE="none"，CONFIG_CPUS=1
soc/soc.conf            SM8850 家族：8 核、Adreno 840
soc/configs/sm8850.config
uefi/README.md          lafaPkg 的位置（还没写，见本机 docs/UEFI-lafa.md）
```

## 怎么用（两分钟）

### 1. 建仓库

打开 <https://github.com/new>：

- Repository name：`hakios-lafa`（随便，好记就行）
- **Public**（私有也行，免费额度 2000 分钟/月，够跑 30+ 次）
- 不要勾 "Add a README"（要空仓库）
- Create repository

### 2. 把内容放进去

**方式 A（拖拽，推荐）**：在新仓库页面点 **Add file → Upload files**，把本目录里的
`lafa` 文件夹和 `.github` 文件夹一起拖进去，Commit changes。

> 如果浏览器把 `.github` 这种点开头的文件夹吞了：用方式 B 单独建那个文件。

**方式 B（手贴 workflow）**：仓库页面 **Add file → Create new file**，文件名填
`.github/workflows/build-kernel.yml`（斜杠会自动建目录），把本目录里同名文件的内容粘进去，Commit。
`lafa/` 里的文件用 "Add file → Upload files" 拖进去即可。

### 3. 看它跑

仓库页面 **Actions** 标签 → 左边 `build lafa kernel` → 最新一次运行。全流程大约 **25–45 分钟**：

| 步骤 | 干什么 | 大约 |
|---|---|---|
| 系统依赖 | clang-14 / lld / llvm / 7zip… | 2 min |
| theos + 工具链 + SDK | cctools 工具链 82 MB + SDK sparse | 3–6 min |
| hakios 源码 + lafa | clone 上游，拷设备包，`hakios-device.py check lafa` | 2 min |
| 配置 | `make lafa_defconfig` + `make config` | 1 min |
| **编内核** | `xnu/apply.sh` + `xnu-deps/setup.sh` + `make kernel` | 15–30 min |
| 编 loader | `make loader-open`（占位身份，不含 Apple 数据） | 1 min |
| 上传产物 | `lafa-kernel` | — |

跑完在运行页底部 **Artifacts → lafa-kernel** 下载，里面是：

```
hakios-kernel          内核（约 15 MB）
loader.efi             XNU loader（EFI 应用，占位身份）
```

### 4. 拿回本机

把 `hakios-kernel` 放进本机 `hakios-lafa\src\hakios-xnu-kernel-main\`（覆盖 `prebuilt/hakios-kernel`
之前先备份），然后：

```bash
# Git Bash，在本机
cd hakios-lafa/src/hakios-xnu-kernel-main
. ../../tools/env.sh
./xl --help
```

**注意**：现在还不能 `fastboot boot` 出画面——缺的是 **UEFI（lafaPkg）**，不是内核。
本机 `docs/UEFI-lafa.md` 写了从 `astonPkg` 派生的步骤；那是下一步。

## 如果 CI 报错

把失败那一步的日志贴给助手（Actions 页面点开红色步骤）。最可能的两处：

- **theos 工具链**下载失败：换成 `L1ghtmann/llvm-project` 的固定 tag，或把 `iOSToolchain-x86_64.tar.xz`
  传到本仓库 Release 再从 Releases 下；
- **`make kernel` 编译错误**：说明设备配置或上游代码在 SM8850 上有问题，日志第一处 `error:` 是关键。
