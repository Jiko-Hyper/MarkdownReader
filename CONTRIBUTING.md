# 插件贡献 / Plugin contributions

新插件开发位于本仓库的 [`Plugins` 分支](https://github.com/Jiko-Hyper/MarkdownReader/tree/Plugins)。

1. Fork 此公开仓库，在自己的副本中从 `Plugins` 创建工作分支。
2. 在 `plugins/` 下添加或修改插件，参考 `plugins/samples/` 与 [插件文档](plugins/README.md)。
3. 提交源码、说明、兼容版本、依赖与许可证信息；说明需要的文件或网络访问权限。不要提交密钥、个人文档或测试数据。
4. 测试后发起 Pull Request，目标仓库为 `Jiko-Hyper/MarkdownReader`，目标分支为 `Plugins`。
5. 由维护者审核代码与安装包哈希后合并。不要绕过受信清单；分支中的代码不代表已通过安全审核。

普通用户可提交 Issue 或 Pull Request，无需直接写入权限。主程序修改请提交到 `main`。

## English

New plugin development lives on the [`Plugins` branch](https://github.com/Jiko-Hyper/MarkdownReader/tree/Plugins).

1. Fork this public repository and create a working branch from `Plugins` in your fork.
2. Add or update a plugin under `plugins/`; consult `plugins/samples/` and the [plugin documentation](plugins/README.md).
3. Include source code, usage instructions, compatible versions, dependencies, licensing, and required file/network permissions. Do not include secrets, personal documents, or test data.
4. Test your changes and open a pull request against `Jiko-Hyper/MarkdownReader`, targeting `Plugins`.
5. Maintainers review the code and package hashes before merging. Do not bypass the trust list. Code on the branch is not automatically security-reviewed or approved for installation.

Anyone may propose changes through issues and pull requests without direct write access. Target `main` for application changes.
