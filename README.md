# jd-spider

## Description | 简介

 一个爬取京东商品信息的Python脚本


## Usage | 使用方法

  1. 安装 `poetry` 包管理器。
```sh
pip install poetry
```
  2. 使用 `poetry` 创建虚拟环境。
```sh
poetry install
```
  3. 修改脚本中 `ITEM_NAME` 处字符串值为需要搜索的关键词。
  4. 执行命令运行脚本。
```sh
poetry run main.py
```
  5. 首次运行时，在弹出的浏览器中登录京东账号，然后回到控制台输入回车。
  6. 开始爬取商品信息。

## Notes | 注意事项

  1. 商品信息存储于 `./data/{ITEM_NAME}.json` 中。
  2. 商品图标存储于 `./data/images/{ITEM_NAME}` 目录中，格式统一为`png`，文件命名格式为 `商铺名+商品名.png`。
  3. 首次登录后会在 `./data` 下生成 `cookies.json` ，保存登录账号的cookies信息，用于后续爬取商品信息，请妥善保管。
  4. 爬取过程中可能会触发滑条验证码，需要手动通过验证，通过后自动恢复爬取流程
  5. ~~代码主要用于提交导论作业，结构混乱还请见谅~~

## Contributors | 贡献者

![Contributors](https://contrib.rocks/image?repo=wyf7685/jd-spider)

~~<u>[原地TP](http://github.com/wyf7685/jd-spider)</u>~~
