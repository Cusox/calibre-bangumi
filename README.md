# Calibre Bangumi 元数据插件

基于 [calibre-douban](https://github.com/fugary/calibre-douban) 对 Bangumi 进行适配的元数据插件，支持从 Bangumi 获取漫画、轻小说信息，适用于 Calibre 桌面端版本

对 [Bangumi API](https://github.com/bangumi/api) v0 版本进行适配，支持漫画/轻小说主条目和单行本的元数据检索（参考 [calibre-web-bgm](https://github.com/JeffersonQin/calibre-web-bgm)）

目前支持的元数据种类有：标题、作者、标签、发行日期、出版方、简介、评分、ISBN 书号、Bangumi ID

## 安装

从 Release 页面下载最新版本的 `calibre_bangumi.zip` 文件，在 Calibre 的插件管理界面安装该 ZIP 文件

## 存在问题

- 使用线程池多线程检索元数据，当条目中存在多本单行本时，可能会遇到网络请求失败的情况，导致元数据获取失败，此时可以尝试重新获取元数据
- 官方提供的 v0 版本搜索接口处于实验阶段，筛选条件较少，可能会导致搜索结果不准确，建议在搜索时尽量提供正确的标题信息以提高搜索准确率
