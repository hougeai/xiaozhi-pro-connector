# 发布 npm 包

包名 `@xiaozhi-pro/connector`（scope）。命令行工具名是 `xiaozhi-pro-connector`（由 `bin` 字段决定，与包名无关）。

## 1. 登录

```bash
npm login
npm whoami
```

`npm login` 会打开浏览器授权；`npm whoami` 输出账号名即成功。项目 `.npmrc` 已指向官方源，不用手动指定。

## 2. 生成 token 填入 `.npmrc`（首次发布或 token 失效时）

npmjs.com → Access Tokens → Generate New Token → **Granular Access Token**

- Packages 选 **Read and write**（可只授权 `@xiaozhi-pro` scope）
- 勾选 **bypass two-factor authentication for automation and CI/CD**（不勾发布必 403）
- 生成后立刻复制（只显示一次），填进本目录 `.npmrc`：

```text
registry=https://registry.npmjs.org
//registry.npmjs.org/:_authToken=npm_xxx
```

## 3. 测试

```bash
npm test
```

## 4. 升版本（代码有更新时才需要，首次发布跳过）

```bash
npm version patch
```

patch=修bug，minor=加功能，major=破坏性变更。npm 禁止重发同版本号，代码改了必须先升版本。

## 5. 检查发布内容

```bash
npm publish --dry-run
```

重点核对两点：清单里没有混入多余文件（如 `.DS_Store`、备份文件）；没有任何 warning。

## 6. 发布

```bash
npm publish --access public
```

## 7. 验证

```bash
npm view @xiaozhi-pro/connector
npm i -g @xiaozhi-pro/connector && xiaozhi-pro-connector --version
```
