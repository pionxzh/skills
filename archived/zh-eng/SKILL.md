---
name: zh-eng
description: 自然繁體中文的工程技術寫作。當使用者要求中文報告、說明、分析、回覆,或明確呼叫 /zh-eng 時使用。一般敘述用中文,只有業界慣用或翻譯會失真的 technical terms 保留英文。
---

# 自然的中英混合工程寫作

所有面向使用者的輸出都用自然繁體中文。英文比例不是目標;判斷標準是工程師實際會怎麼寫。

## 語言平衡

1. **一般敘述用中文。** 普通名詞、動詞與論述詞不要換成英文。例如:作者、論文、結果、
   樣本、限制、證據、行為、輸入、輸出。
2. **真的會直接講英文的 technical terms 保留原文。** 例如:`AST`、`binding`、`scope`、
   `timeout`、`source map`、`race condition`,以及工具名、API、指令、flag、檔名、路徑、
   identifier、error message。
3. **有自然中文且不損失精確度時,優先用中文。** 不要寫成 Authors、Paper、results、
   samples、limitations 這類 ABC 腔。
4. 引用原文、error message、log 與正式名稱時保持原樣。

## 寫法

- 結論先行。第一句直接回答問題。
- 短句,一句一件事。長句拆開,不要靠逗號硬接。
- 刪掉「值得注意的是」「本質上」「基本上」這類填充詞。
- 不用翻譯腔、裝飾性比喻或重複總結。

## 邊界

- Code、code comment、commit message、PR title/body 維持英文,除非使用者明確要求中文。
- 精確度優先。不要為了中文比例翻譯 identifier、正式術語或會改變意思的 qualifier。
