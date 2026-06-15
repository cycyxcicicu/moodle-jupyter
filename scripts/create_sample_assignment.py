import sys
import os
import nbformat as nbf

def create_sample(dest_path):
    nb = nbf.v4.new_notebook()
    
    # Cấu hình kernelspec Python 3 bắt buộc để tự động khớp kernel khi chấm bài
    nb.metadata['kernelspec'] = {
        "display_name": "Python 3 (ipykernel)",
        "language": "python",
        "name": "python3"
    }
    
    # 1. Cell markdown: Đề bài
    cell_desc = nbf.v4.new_markdown_cell(
        "# Bài tập 1: Hàm cộng hai số\n\n"
        "Hãy hoàn thành hàm `add(a, b)` dưới đây để trả về tổng của hai số."
    )
    
    # 2. Cell code: Solution cell (chứa code của học sinh)
    cell_sol = nbf.v4.new_code_cell(
        "def add(a, b):\n"
        "    # Viết code của bạn ở đây\n"
        "    # YOUR CODE HERE\n"
        "    raise NotImplementedError()"
    )
    # Metadata nbgrader cho Solution cell
    cell_sol.metadata['nbgrader'] = {
        "grade": False,
        "grade_id": "add_solution",
        "locked": False,
        "schema_version": 3,
        "solution": True,
        "task": False
    }
    
    # 3. Cell code: Test cell (chấm điểm tự động)
    cell_test = nbf.v4.new_code_cell(
        "# Các test case chấm điểm tự động\n"
        "assert add(1, 2) == 3, 'Lỗi: 1 + 2 phải bằng 3'\n"
        "assert add(-1, 1) == 0, 'Lỗi: -1 + 1 phải bằng 0'\n"
        "print('Chúc mừng! Tất cả các test case đều vượt qua.')"
    )
    # Metadata nbgrader cho Grade cell (locked = True, points = 2)
    cell_test.metadata['nbgrader'] = {
        "grade": True,
        "grade_id": "add_test",
        "locked": True,
        "points": 2,
        "schema_version": 3,
        "solution": False,
        "task": False
    }
    
    nb.cells = [cell_desc, cell_sol, cell_test]
    
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, 'w', encoding='utf-8') as f:
        nbf.write(nb, f)
    print(f"Đã tạo notebook bài tập mẫu có kernelspec Python 3 tại: {dest_path}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Sử dụng: python3 create_sample_assignment.py <đường_dẫn_đến_file_ps1.ipynb>")
        sys.exit(1)
    create_sample(sys.argv[1])
