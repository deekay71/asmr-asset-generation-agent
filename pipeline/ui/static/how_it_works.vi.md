# Shine It V6 hoạt động như thế nào

Shine It là pipeline tạo asset bằng AI cho game ASMR dọn dẹp một-vật-thể. Nó biến tài liệu thiết kế game thành chuỗi ảnh bẩn→sạch đầy đủ, kèm sprite công cụ và overlay rác, rồi học từ phản hồi của bạn để mỗi vòng tạo lại đều tốt hơn vòng trước.

---

## Tổng quan

```
items_config.json (GDD của bạn)
       │
       ▼
prompt_agent.compose()   ──── đọc step_patterns.json (bộ não)
       │                          ▲
       ▼                          │  phản hồi được chưng cất ở đây
i2i_backend (Vertex Flash / Fal NB-2 / Pro)
       │
       ▼
projects/level_NN/staging/*.png
       │
       ▼
phase_5 hậu xử lý (xoá nền, crop)
       │
       ▼
duyệt (UI này) → approved_ids.json + regen_queue.json
       │                              │
       ▼                              ▼
phase_7 hoàn thiện           --learn → step_patterns.json
```

---

## Các giai đoạn

| Giai đoạn | Mục đích |
|---|---|
| **1 Ảnh gốc** | Tạo ảnh tham chiếu "sạch hoàn hảo" cho cấp độ. |
| **1b Sub-flow** | Composite anchor cho vật mở được (lọc điều hoà, keycap bàn phím). |
| **2 Kiểm tra** | Check tĩnh miễn phí items_config.json. Luôn chạy trước phase 3. |
| **3 Chuỗi** | Sự kiện chính — chuỗi bẩn→sạch qua I2I, theo `source_state`. |
| **3b Sprite/Rác** | Phần rời: overlay rác, subpart, background, style variant. |
| **4 Công cụ** | Sprite công cụ (cọ, máy thổi, dụng cụ cạo). |
| **5 Hậu xử lý** | Xoá nền + crop chặt theo alpha. |
| **6 Duyệt** | Tab Duyệt của UI này. Ba lựa chọn mỗi asset: duyệt / loại / tạo lại+nhận xét. |
| **7 Hoàn thiện** | Copy staging đã duyệt sang `final/`. |

---

## Prompt agent (tính năng chủ lực V6)

Thay vì một prompt envelope 3,500 ký tự, V6 dựng prompt gọn ~600 ký tự bằng **template slot** chọn theo `step_type` của asset (`dirty_base`, `foam_application`, `tool_sprite`, …). Template nằm ở `pipeline/prompt_agent/templates/`.

Mỗi prompt được ghép còn thêm:
- **`best_practices`** vĩnh viễn của pattern khớp
- **`forbid`** (anti-pattern) của pattern đó
- và (nếu có) **nhận xét tạo-lại** của bạn cho asset đó

Xem prompt đã ghép trước khi tạo — tab **Tạo ảnh → 🔍 Xem prompt**.

---

## Vòng học

1. Đánh dấu asset ở tab **Duyệt** và bấm **Lưu kết quả**.
2. Bấm **Chạy --learn**. Mỗi nhận xét tạo-lại được gửi tới Gemini Flash text-only, chưng cất thành quy tắc cấu trúc (polarity + clause).
3. Quy tắc trở thành **ứng viên** dưới `step_type` của asset trong `step_patterns.json`.
4. Khi cùng quy tắc xuất hiện **lần thứ hai** ở vòng duyệt sau, nó được **thăng cấp** thành `best_practices` hoặc `forbid` vĩnh viễn.
5. Lần `compose()` sau tự động dùng nó. Agent giỏi lên, vĩnh viễn.

Chỉnh thủ công bộ não ở tab **Bộ nhớ**.

---

## Tệp đặt ở đâu

```
projects/level_NN_name/
├── items_config.json          ← GDD của bạn
├── staging/*.png              ← đầu ra, trước hậu xử lý
├── final/*.png                ← sau hậu xử lý (xoá nền, crop)
├── approved/*.png             ← đã hoàn thiện (phase 7)
├── approved_ids.json          ← Duyệt ghi vào đây
├── regen_queue.json           ← Duyệt ghi vào đây
├── cost_log.jsonl             ← chi phí mỗi lần gọi
└── agent_log.jsonl            ← quyết định compose()
pipeline/step_patterns.json    ← bộ nhớ vĩnh viễn của agent
```

---

## Backend

- **`google_flash`** (mặc định) — `gemini-2.5-flash-image` qua Vertex. Nhanh, hiểu hướng dẫn tốt, rẻ.
- **`fal_nb2`** — Fal Nano-Banana 2 Edit. Chuẩn chất lượng.
- **`fal_nb_pro`** — Fal Nano-Banana Pro. Tốt nhất cho anchor hero, chậm và đắt hơn.

Chọn ở dropdown trong tab Tạo ảnh.
