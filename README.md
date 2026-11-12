# CCTA Alert

Công cụ hỗ trợ chỉ định CT mạch vành, chạy trên Windows, dùng cùng phần mềm HIS.
Quy tắc đánh giá: bảng RF-CL của ESC 2024 (tuổi × giới × điểm triệu chứng × số yếu tố nguy cơ).

Viết bằng Python 3 + tkinter. Kết nối HIS (Oracle) dùng thư viện `oracledb`.

## Chạy

- Từ mã nguồn: `python main.py` (cần `pip install oracledb`)
- Bản đóng gói: `dist\CctaAlert.exe` (máy dùng không cần cài Python hay Oracle Client)
- Đóng gói lại: chạy `build_exe.bat` (cần `pip install pyinstaller oracledb`)
- Icon (chữ CCTA + đường nhịp tim) nằm ở `ccta_alert/assets/ccta.ico`, được gắn vào exe và cửa sổ. Muốn đổi kiểu thì sửa rồi chạy `python tools/make_icon.py` (cần Pillow), sau đó đóng gói lại.

## Kết nối HIS (chỉ đọc)

App đọc **danh sách phòng khám** và **bệnh nhân khám hôm nay tại phòng đang chọn** từ HIS, theo cấu hình trong
file `his.json` cạnh `CctaAlert.exe`. App không có dữ liệu mẫu: chưa cấu hình thì chưa có phòng khám và bệnh nhân
nào, và màn chọn phòng khám có dòng "Bấm vào đây để mở Kết nối HIS".

Cài đặt (trên giao diện):

1. Menu ⋯ → **Kết nối HIS** (hoặc tab "Kết nối HIS" trong cửa sổ Quản lý).
2. Điền máy chủ, cổng, service name (hoặc chuỗi DSN), tài khoản, mật khẩu.
3. Bấm **Kiểm tra kết nối**. App chạy thử đúng 2 câu SELECT và báo từng bước: kết nối, danh sách phòng khám,
   câu truy vấn bệnh nhân. Lỗi thường gặp (sai mật khẩu, sai service, thiếu quyền, sai tên cột…) có gợi ý tiếng Việt.
4. Bấm **Lưu**, rồi **Khởi động lại app để áp dụng**.

- Mật khẩu được **mã hóa** (Windows DPAPI) trước khi lưu, chỉ giải mã được trên đúng máy đó. Ô mật khẩu để trống
  khi lưu lại nghĩa là giữ mật khẩu cũ. Chép `his.json` sang máy khác sẽ không dùng được, phải nhập lại mật khẩu.
- "Đọc lại mỗi (giây)": bao lâu đọc lại danh sách bệnh nhân (mặc định 10, tối thiểu 5). Mỗi lần đọc, nếu có
  START_TIME mới hơn mọi lần trước (bác sĩ vừa bắt đầu xử lý bệnh nhân trên HIS), khung tự chuyển sang bệnh nhân đó.
  Mở danh sách chọn bệnh nhân cũng đọc lại ngay.
- Database cũ (Oracle 11g) không hỗ trợ chế độ "thin": cài Oracle Instant Client và điền thư mục của nó.
- Bỏ chọn "Bật kết nối HIS" để tạm tắt kết nối.
- Vẫn có thể sửa tay file `his.json` (mẫu: `dist\his.example.json`). Mật khẩu viết thường trong file sẽ được
  app mã hóa ở lần đọc đầu.

**Chỉ đọc, không thao tác gì vào database:**

- Trong app chỉ có 4 câu SELECT viết sẵn (`ccta_alert/his.py`): danh sách phòng (`HIS_EXECUTE_ROOM`),
  bệnh nhân khám hôm nay tại phòng (`HIS_TREATMENT` + `HIS_SERVICE_REQ`) và báo cáo dịch vụ của tab Xuất báo cáo,
  lịch sử dịch vụ của một bệnh nhân (thêm `HIS_SERE_SERV`). Ngày, mã phòng và mã dịch vụ truyền bằng bind variable.
- Trước khi chạy, app kiểm tra câu lệnh: phải là SELECT, không có `;`, không có INSERT / UPDATE / DELETE / MERGE /
  DDL / `FOR UPDATE`… Không đạt là không chạy.
- Không bao giờ commit. Mỗi lần đọc mở kết nối rồi đóng ngay. Có giới hạn 5000 dòng (báo cáo: 20000 dòng) và 20 giây mỗi lần đọc.
- **Nên tạo tài khoản Oracle riêng chỉ có quyền SELECT** trên 4 bảng trên. Đây là lớp bảo vệ chắc chắn nhất, vì
  nó do database đảm bảo chứ không phụ thuộc vào app.

Cách app dùng dữ liệu HIS:

- Mã phòng: lấy `HIS_EXECUTE_ROOM.ROOM_ID` để lọc `HIS_SERVICE_REQ.EXECUTE_ROOM_ID` (như hai câu SQL gốc).
- Mã BN lấy từ `HIS_TREATMENT.TDL_PATIENT_CODE`, dùng để nhận ra bệnh nhân "đã được chỉ định chụp" giữa các lần khám.
- Mã điều trị lấy từ `HIS_TREATMENT.TREATMENT_CODE`: hiện cạnh mã BN, tìm được trong danh sách bệnh nhân,
  ghi vào `alerts.jsonl` (trường `treatment_code`) và cột "Mã điều trị" ở nhật ký Thống kê.
- Nếu HIS không có cột `TDL_PATIENT_CODE` (lỗi ORA-00904), app tự dùng câu dự phòng không có cột đó và lấy
  mã điều trị làm mã nhận diện. Khi đó dòng "đã được chỉ định chụp" chỉ nhận ra trong cùng một đợt điều trị.
- Tuổi tính từ ngày sinh đầy đủ (`TDL_PATIENT_DOB`). Giới tính: 1 = Nữ, 2 = Nam.
- HIS thiếu ngày sinh hoặc giới tính thì app báo và không cho tính RF-CL cho bệnh nhân đó.
- Bệnh nhân mới vào phòng trong lúc khung đang thu gọn thì khung tự mở ra với bệnh nhân đó.
  Khung đang mở (bác sĩ đang làm dở) thì không bị ngắt, bệnh nhân mới chỉ thêm vào danh sách.
- Mất kết nối: danh sách bệnh nhân đã đọc vẫn giữ, app báo lỗi đỏ trong danh sách và ở thanh trạng thái
  cửa sổ Quản lý, rồi tự thử lại ở lần đọc sau.

## Cách dùng

HIS mở toàn màn hình. CCTA Alert là một khung nhỏ luôn nằm trên cùng ở **góc dưới bên phải**.

0. Khi mở app, bác sĩ **bắt buộc nhập mã bác sĩ** (chữ không dấu, số, `.` `_` `-`; tự viết hoa), rồi
   **bắt buộc chọn phòng khám**. App không điền sẵn mã lần trước (máy dùng chung), chỉ hiện dòng
   "Dùng mã lần trước: …" để bấm chọn. Đổi bác sĩ: menu ⋯ → Đổi bác sĩ.
   Chọn phòng khám (gõ để tìm, ↑↓ rồi Enter, hoặc bấm chuột). App nhớ phòng
   khám lần trước và đưa lên đầu danh sách, nhưng vẫn phải xác nhận. Cảnh báo và quyết định được ghi cho phòng khám này.
   Muốn đổi thì vào menu ⋯ → Đổi phòng khám.
1. Khi có bệnh nhân mới vào khám tại phòng trên HIS (và khung đang thu gọn), khung tự mở ra với tên, tuổi,
   giới của bệnh nhân.
2. Bác sĩ chỉ chọn **điểm triệu chứng** (0–3) và **số yếu tố nguy cơ** (0–5), rồi bấm **Xác nhận**.
   Chưa chọn đủ thì không xác nhận được.
3. Cảnh báo hiện ngay trong khung: tỷ lệ % theo RF-CL, mức Rất thấp / Thấp / Vừa.
   Nếu bệnh nhân **đã được chỉ định chụp** trước đó, khung hiện dòng xanh ghi ngày, phòng khám, bác sĩ đã chỉ định
   (ở màn nhập liệu, màn kết quả và trong danh sách chọn bệnh nhân) để không chỉ định lại.
4. Bác sĩ bấm **Chỉ định chụp** hoặc **Không chỉ định** (chọn lý do). Quyết định được ghi vào thống kê, khung tự thu gọn.

Trên khung: bấm thanh tiêu đề để thu gọn hoặc mở, kéo thanh tiêu đề để dời chỗ.

**Chọn bệnh nhân nhanh:** bấm nút hình người trên thanh tiêu đề hoặc chữ "Đổi bệnh nhân" cạnh tên.
Danh sách gồm bệnh nhân khám hôm nay tại phòng khám đang làm việc, người vào khám gần nhất lên đầu. Gõ tên, mã BN hoặc mã điều trị để lọc (gõ không dấu được, ví dụ "hung", "1287"), dùng ↑↓ để chọn, Enter để mở, Esc để đóng và quay lại màn đang xem.

Nút menu (⋯) mở cửa sổ quản lý gồm Thống kê, Xuất báo cáo, Quy tắc đánh giá, Kết nối HIS.
Mục "Chọn bệnh nhân…" trong menu mở cùng danh sách đó.

**Đã thực hiện dịch vụ chưa:** khi mở một bệnh nhân, app hỏi HIS (theo mã bệnh nhân, mọi thời điểm) xem bệnh nhân đã
từng có y lệnh với các mã dịch vụ trong danh sách của tab Xuất báo cáo chưa, và hiện nhãn cạnh tên:
"Chưa thực hiện", "Đã thực hiện dd/mm/yyyy" (lần gần nhất; "Đã chỉ định" nếu y lệnh chưa kết thúc), viền cam kèm ⚠ nếu
trong vòng 2 năm. Bấm vào nhãn để xem các lần gần nhất. Kết quả được dùng lại trong 2 phút.

**Xuất báo cáo:** chọn khoảng ngày (tối đa 366 ngày), sửa danh sách mã dịch vụ nếu cần (mặc định
`HA-N0300-043, HA-N0200-001, HA-N0300-027, HA-N0300-042`; bấm "Lưu danh sách mã" để giữ cho lần sau, lưu trong
`settings.json`), bấm **Lấy dữ liệu từ HIS**, rồi **Xuất Excel…** (.xlsx) hoặc **Xuất CSV…**. Lọc theo
`sr.START_TIME` trong khoảng ngày, bỏ bản ghi đã xóa (`IS_DELETE = 0`).
Mỗi dòng được đối chiếu với quyết định lưu trên máy (`alerts.jsonl`, gồm cả dữ liệu đã gộp từ máy khác) và tô màu:
không màu = **Không cảnh báo** (không có quyết định nào), xanh lá = **Cảnh báo – được chụp**, đỏ = **Cảnh báo – không
được chụp** (kèm mức, thời gian, mã bác sĩ, lý do). Nối theo mã điều trị; bản ghi cũ chưa có mã điều trị thì nối theo
mã bệnh nhân với cảnh báo trong 30 ngày trước y lệnh. File Excel giữ nguyên màu. Muốn đủ ca của mọi phòng khám thì
gộp dữ liệu các máy trước (màn Thống kê).

Tab Xuất báo cáo có 2 mục (chọn ở góc phải): **Y lệnh dịch vụ (HIS)** như trên, và **Danh sách cảnh báo**: các quyết định
lưu trên máy (không cần HIS), lọc theo khoảng ngày, phòng khám và quyết định (Tất cả / Được chụp / Không được chụp),
xanh lá = được chụp, đỏ = không được chụp, xuất Excel (giữ màu) hoặc CSV. Bảng tự cập nhật khi có quyết định mới.
Bấm **Đối chiếu với HIS** để so từng quyết định với y lệnh trên HIS (dùng danh sách mã dịch vụ của mục Y lệnh
dịch vụ, đọc từ ngày đầu đến 30 ngày sau ngày cuối): **Khớp** khi được chụp và có y lệnh, hoặc không được chụp và không
có y lệnh; **Lệch** (chữ đậm, ⚠) khi được chụp mà HIS không có y lệnh, hoặc không được chụp mà HIS lại có y lệnh.
Nối theo mã điều trị, không có thì theo mã bệnh nhân với y lệnh trong 30 ngày kể từ ngày cảnh báo. Lọc được Khớp / Lệch;
mã y lệnh, dịch vụ, thời gian y lệnh có trong bảng và file xuất.

## Dữ liệu lưu trên máy

Chưa dùng cơ sở dữ liệu. Dữ liệu lưu **cùng thư mục với `CctaAlert.exe`** (chạy từ mã nguồn bằng `python main.py`
thì là thư mục `dist`). Đường dẫn đang dùng hiện ở góc dưới bên trái cửa sổ Quản lý.

```
dist\
  CctaAlert.exe
  alerts.jsonl
  settings.json
  his.json            (nếu dùng kết nối HIS)
  his.example.json    (mẫu)
```

- Đặt exe ở thư mục ghi được (ví dụ `D:\CctaAlert\` hoặc Desktop). Nếu thư mục đó không ghi được
  (ví dụ `C:\Program Files\...`), app tự lưu sang `%APPDATA%\CctaAlert` của người dùng Windows đang chạy.
- Chạy `build_exe.bat` đóng gói lại chỉ thay `CctaAlert.exe`, không đụng tới hai file dữ liệu.
  **Đừng xóa thư mục `dist`** khi dọn dẹp, vì sẽ mất dữ liệu.
- Mang exe sang máy khác thì chỉ chép `CctaAlert.exe`. Chép cả `alerts.jsonl` sẽ mang theo dữ liệu của máy cũ.

Các file:

- `alerts.jsonl`: mỗi lần bác sĩ bấm **Chỉ định chụp** hoặc **Không chỉ định**, app ghi thêm một dòng
  (giờ, phòng khám, mã bác sĩ, mã và tên BN, mức cảnh báo, %, quyết định, lý do). File được ghi ngay lúc bấm,
  nên tắt app hay mất điện cũng không mất. Mở app sẽ đọc lại file này cho Thống kê và dòng "đã được chỉ định chụp".
  Nếu một dòng bị hỏng (ví dụ mất điện đúng lúc ghi), app bỏ qua dòng đó và đọc tiếp phần còn lại.
- `settings.json`: mã bác sĩ và phòng khám dùng lần trước.

Sao lưu: chép `alerts.jsonl` và `settings.json` sang chỗ khác. Mọi người dùng Windows trên cùng máy dùng chung
một file (nếu cùng chạy một exe); mỗi máy có file riêng.

Biến môi trường `CCTA_DATA_DIR` (không bắt buộc) dùng để chỉ định thư mục dữ liệu khác, ví dụ khi kiểm tra.

### Gộp dữ liệu nhiều máy để thống kê chung

Mỗi máy có `alerts.jsonl` riêng. Để có thống kê chung trên một máy:

1. Ở mỗi máy, chép `alerts.jsonl` ra (USB, thư mục chia sẻ…) và **đổi tên theo máy**, ví dụ
   `alerts_PK15.jsonl`, `alerts_PK16.jsonl`, để không ghi đè lên nhau.
2. Ở máy thống kê: cửa sổ Quản lý → **Thống kê** → **Gộp dữ liệu từ máy khác…** → chọn cùng lúc các file đó.
3. App báo từng file: bao nhiêu bản ghi mới, bao nhiêu trùng (bỏ qua), bao nhiêu dòng hỏng.

- Bản ghi đã có thì không thêm lần nữa, nên định kỳ (ví dụ cuối tháng) cứ chép file mới nhất của các máy về
  gộp lại, không bị đếm đôi. Trùng được nhận theo: giờ ra quyết định, mã BN, mã bác sĩ, phòng khám, quyết định.
- Bản ghi gộp vào được ghi thêm vào `alerts.jsonl` của máy thống kê, nên mở lại app vẫn còn.
- Từ bản này, mỗi bản ghi có thêm tên máy (`machine`). Dòng "Dữ liệu: … từ N máy" trên màn Thống kê cho biết
  đã gộp từ những máy nào. Bản ghi cũ (trước bản này) không có tên máy nhưng vẫn gộp và thống kê bình thường.
- Không nên cho nhiều máy cùng ghi vào **một** file trên thư mục mạng (đặt `CCTA_DATA_DIR` chung):
  hai máy ghi cùng lúc có thể làm hỏng dòng. Mỗi máy giữ file riêng rồi gộp như trên là an toàn hơn.

## Cấu trúc mã

```
main.py                    điểm khởi chạy
ccta_alert/
  rules.py                 bảng RF-CL, cách tính điểm, mức cảnh báo
  data.py                  lưu trên máy (cạnh CctaAlert.exe), bệnh nhân, bản ghi cảnh báo (Store)
  his.py                   đọc HIS (Oracle) CHỈ ĐỌC: 2 câu SELECT, kiểm tra câu lệnh, mã hóa mật khẩu, chạy nền
  connection_page.py       tab "Kết nối HIS": điền, kiểm tra, lưu thông tin kết nối
  alert_panel.py           khung cảnh báo góc dưới phải (nhập liệu + kết quả)
  pages.py                 các màn quản lý: Thống kê, Quy tắc
  report_page.py           tab "Xuất báo cáo": y lệnh theo danh sách mã dịch vụ từ HIS → Excel/CSV
  widgets.py               thành phần giao diện dùng chung (thẻ, nút, biểu đồ, bảng RF-CL)
  theme.py                 màu sắc, font chữ
  app.py                   khởi chạy, cửa sổ quản lý
```

## Tham số dùng khi kiểm tra

- `--page N`: mở cửa sổ quản lý ở màn N (0 Thống kê, 1 Xuất báo cáo, 2 Quy tắc, 3 Kết nối HIS).
- `--snap file.png`: tự chụp ra ảnh PNG rồi thoát (cần Pillow).
