#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load biến môi trường
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
# Đảm bảo file config LTI tồn tại
./scripts/ensure-generated-env.sh

POSTGRES_ADMIN_USER=${POSTGRES_ADMIN_USER:-postgres_user}
MOODLE_DB_NAME=${MOODLE_DB_NAME:-moodle}
JUPYTERHUB_DB_NAME=${JUPYTERHUB_DB_NAME:-jupyterhub}
MOODLE_HOST_PORT=${MOODLE_HOST_PORT:-18080}
JUPYTERHUB_HOST_PORT=${JUPYTERHUB_HOST_PORT:-18000}
POSTGRES_HOST_PORT=${POSTGRES_HOST_PORT:-15434}

echo "=== ĐANG CHẠY CHẨN ĐOÁN HỆ THỐNG (DOCTOR CHECK) ==="
status=0

# 1. Kiểm tra Docker Compose Services
echo -n "1. Kiểm tra trạng thái các service: "
postgres_status=$(docker ps --filter name=infra-postgres --filter status=running -q | head -n 1)
moodle_status=$(docker compose ps -q moodle)
jupyterhub_status=$(docker compose ps -q jupyterhub)
assignment_status=$(docker compose ps -q jupyter-assignment-service)

if [ -n "$postgres_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$postgres_status")" = "true" ]; then
    echo -n "[postgres: RUNNING] "
else
    echo -n "[postgres: STOPPED/ERROR] "
    status=1
fi

if [ -n "$moodle_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$moodle_status")" = "true" ]; then
    echo -n "[moodle: RUNNING] "
else
    echo -n "[moodle: STOPPED/ERROR] "
    status=1
fi

if [ -n "$jupyterhub_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$jupyterhub_status")" = "true" ]; then
    echo -n "[jupyterhub: RUNNING] "
else
    echo -n "[jupyterhub: STOPPED/ERROR] "
    status=1
fi

if [ -n "$assignment_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$assignment_status")" = "true" ]; then
    echo "[assignment-service: RUNNING]"
else
    echo "[assignment-service: STOPPED/ERROR]"
    status=1
fi

# 2. Kiểm tra database trong postgres
if [ -n "$postgres_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$postgres_status")" = "true" ]; then
    echo -n "2. Kiểm tra database moodle, jupyterhub và assignment_service: "
    moodle_db_exists=$(docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$MOODLE_DB_NAME'" 2>/dev/null || echo "0")
    jupyterhub_db_exists=$(docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$JUPYTERHUB_DB_NAME'" 2>/dev/null || echo "0")
    assignment_db_exists=$(docker exec -i infra-postgres psql -U "$POSTGRES_ADMIN_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${ASSIGNMENT_DB_NAME:-assignment_service}'" 2>/dev/null || echo "0")
    
    if [ "$moodle_db_exists" = "1" ] && [ "$jupyterhub_db_exists" = "1" ] && [ "$assignment_db_exists" = "1" ]; then
        echo "OK (Cả ba DB tồn tại)"
    else
        echo "LỖI (Thiếu database! moodle: $moodle_db_exists, jupyterhub: $jupyterhub_db_exists, assignment_service: $assignment_db_exists)"
        status=1
    fi
else
    echo "2. Kiểm tra database: Bỏ qua vì Postgres không chạy."
    status=1
fi

# 3. Kiểm tra file generated/lti.env và đối chiếu DB/Container
echo "3. Kiểm tra file generated/lti.env và đối chiếu DB/Container:"
if [ -f "generated/lti.env" ] && [ -s "generated/lti.env" ]; then
    echo "   - File generated/lti.env: OK (Tồn tại và không rỗng)"
    
    # Kiểm tra các biến LTI13_* bắt buộc trong file
    set +e
    lti_client_id=$(grep "^LTI13_CLIENT_ID=" generated/lti.env | cut -d= -f2- | tr -d '\r')
    set -e
    
    missing_var=0
    required_vars=("LTI13_CLIENT_ID" "LTI13_ISSUER" "LTI13_AUTHORIZE_URL" "LTI13_TOKEN_URL" "LTI13_JWKS_URL" "LTI13_REDIRECT_URI" "LTI13_LAUNCH_URL")
    for var in "${required_vars[@]}"; do
        val=$(grep "^${var}=" generated/lti.env | cut -d= -f2- | tr -d '\r')
        if [ -z "$val" ]; then
            echo "   - Biến $var trong file: LỖI (Thiếu hoặc rỗng!)"
            missing_var=1
        else
            echo "   - Biến $var trong file: OK"
        fi
    done
    
    if [ $missing_var -eq 1 ]; then
        status=1
    fi
    
    # So khớp LTI13_CLIENT_ID với database và kiểm tra cấu hình LTI nếu Postgres đang chạy
    if [ -n "$postgres_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$postgres_status")" = "true" ]; then
        DB_USER=${POSTGRES_ADMIN_USER:-postgres_user}
        DB_PASS=${POSTGRES_ADMIN_PASSWORD:-postgres_password}
        DB_NAME=${MOODLE_DB_NAME:-moodle}
        
        # Lấy thông tin typeid và clientid từ database
        db_tool_info=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT id, clientid FROM mdl_lti_types WHERE name = 'JupyterHub' ORDER BY id LIMIT 1;" 2>/dev/null || echo "")
        
        if [ -n "$db_tool_info" ]; then
            db_typeid=$(echo "$db_tool_info" | cut -d'|' -f1)
            db_clientid=$(echo "$db_tool_info" | cut -d'|' -f2)
            
            # 1. So khớp Client ID
            if [ "$lti_client_id" = "$db_clientid" ]; then
                echo "   - Khớp Client ID (file lti.env vs DB): OK ($lti_client_id)"
            else
                echo "   - Khớp Client ID (file lti.env vs DB): LỖI (Trong file: $lti_client_id, Database: $db_clientid)"
                status=1
            fi
            
            # Truy vấn các cấu hình LTI chi tiết
            db_toolurl=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$db_typeid' AND name = 'toolurl';" 2>/dev/null || echo "")
            db_keytype=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$db_typeid' AND name = 'keytype';" 2>/dev/null || echo "")
            db_publickeyset=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$db_typeid' AND name = 'publickeyset';" 2>/dev/null || echo "")
            db_redirectionuris=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT value FROM mdl_lti_types_config WHERE typeid = '$db_typeid' AND name = 'redirectionuris';" 2>/dev/null || echo "")
            
            # 2. Kiểm tra keytype phải là JWK_KEYSET
            if [ "$db_keytype" = "JWK_KEYSET" ]; then
                echo "   - Cấu hình keytype trong Database: OK (JWK_KEYSET)"
            else
                echo "   - Cấu hình keytype trong Database: LỖI (Kỳ vọng JWK_KEYSET, thực tế: $db_keytype)"
                status=1
            fi
            
            # 3. Kiểm tra publickeyset không rỗng
            if [ -n "$db_publickeyset" ]; then
                echo "   - Cấu hình publickeyset trong Database: OK ($db_publickeyset)"
            else
                echo "   - Cấu hình publickeyset trong Database: LỖI (Bị rỗng!)"
                status=1
            fi
            
            # 4. Kiểm tra redirectionuris chỉ có 1 callback URI
            line_count=$(echo "$db_redirectionuris" | grep -c "^")
            has_slash_uri=$(echo "$db_redirectionuris" | grep -F "oauth_callback/" || true)
            if [ "$line_count" -eq 1 ] && [ -z "$has_slash_uri" ]; then
                echo "   - Cấu hình redirectionuris trong Database: OK ($db_redirectionuris)"
            else
                echo "   - Cấu hình redirectionuris trong Database: LỖI (Phải chỉ có 1 dòng và không có slash kết thúc, thực tế: $(echo "$db_redirectionuris" | tr '\n' ' '))"
                status=1
            fi
            
            # 5. Kiểm tra trùng lặp cấu hình trong lti_types_config
            duplicates=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT name, COUNT(*) FROM mdl_lti_types_config WHERE typeid = '$db_typeid' GROUP BY name HAVING COUNT(*) > 1;" 2>/dev/null || echo "")
            if [ -z "$duplicates" ]; then
                echo "   - Không có cấu hình trùng lặp trong Database: OK"
            else
                echo "   - Có cấu hình trùng lặp trong Database: LỖI (Trùng lặp: $(echo "$duplicates" | tr '\n' ' '))"
                status=1
            fi
            
            # 6. Kiểm tra các config key có tiền tố lti_ (không được tồn tại)
            db_lti_prefix_count=$(docker exec -i infra-postgres env PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM mdl_lti_types_config WHERE typeid = '$db_typeid' AND name LIKE 'lti_%';" 2>/dev/null || echo "0")
            if [ "$db_lti_prefix_count" = "0" ]; then
                echo "   - Không có cấu hình tiền tố lti_ trong Database: OK"
            else
                echo "   - Có cấu hình tiền tố lti_ trong Database: LỖI (Tìm thấy $db_lti_prefix_count cấu hình tiền tố lti_!)"
                status=1
            fi
            
            # 7. Kiểm tra các biến môi trường thực tế bên trong container jupyterhub
            if [ -n "$jupyterhub_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$jupyterhub_status")" = "true" ]; then
                container_vars=$(docker compose exec -T jupyterhub env | grep "^LTI13_" 2>/dev/null || echo "")
                if [ -n "$container_vars" ]; then
                    echo "   - Đọc biến môi trường trong container jupyterhub: OK"
                    
                    # Kiểm tra từng biến trong container
                    container_missing=0
                    for var in "${required_vars[@]}"; do
                        val=$(echo "$container_vars" | grep "^${var}=" | cut -d= -f2- | tr -d '\r' || true)
                        if [ -z "$val" ]; then
                            echo "     + Biến $var trong container: LỖI (Thiếu hoặc rỗng!)"
                            container_missing=1
                        else
                            echo "     + Biến $var trong container: OK"
                        fi
                    done
                    
                    if [ $container_missing -eq 1 ]; then
                        status=1
                    else
                        # Khớp Client ID trong container với DB
                        container_client_id=$(echo "$container_vars" | grep "^LTI13_CLIENT_ID=" | cut -d= -f2- | tr -d '\r' || true)
                        if [ "$container_client_id" = "$db_clientid" ]; then
                            echo "   - Khớp Client ID trong container với Database: OK ($container_client_id)"
                        else
                            echo "   - Khớp Client ID trong container với Database: LỖI (Container: $container_client_id, Database: $db_clientid)"
                            status=1
                        fi
                    fi
                else
                    echo "   - Đọc biến môi trường trong container jupyterhub: LỖI (Không tìm thấy biến LTI13_* nào!)"
                    status=1
                fi
            else
                echo "   - Đọc biến môi trường trong container jupyterhub: Bỏ qua (JupyterHub container không chạy)"
            fi
            
            # 8. Kiểm tra log jupyterhub xem có cảnh báo lỗi cấu hình cookie_options không
            if [ -n "$jupyterhub_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$jupyterhub_status")" = "true" ]; then
                warn_count=$(docker compose logs jupyterhub 2>&1 | grep -c "Config option cookie_options not recognized" || true)
                if [ "$warn_count" = "0" ]; then
                    echo "   - Kiểm tra log JupyterHub (không bị lỗi cookie_options): OK"
                else
                    echo "   - Kiểm tra log JupyterHub (bị lỗi cookie_options): LỖI (Tìm thấy $warn_count cảnh báo 'Config option cookie_options not recognized'!)"
                    status=1
                fi
            fi
        else
            echo "   - Cấu hình LTI JupyterHub trong DB: LỖI (Không tìm thấy LTI tool 'JupyterHub' trong Database!)"
            status=1
        fi
    else
        echo "   - Kiểm tra DB & Container: Bỏ qua (Postgres không chạy)"
    fi
else
    echo "   - File generated/lti.env: LỖI (Không tồn tại hoặc rỗng!)"
    status=1
fi

# 4. Kiểm tra port trên máy host đang lắng nghe
echo "4. Kiểm tra các cổng host đang hoạt động:"
if command -v nc >/dev/null 2>&1; then
    if nc -z -w 2 127.0.0.1 "$POSTGRES_HOST_PORT" >/dev/null 2>&1; then
        echo "   - Cổng Postgres $POSTGRES_HOST_PORT: OK (Đang lắng nghe)"
    else
        echo "   - Cổng Postgres $POSTGRES_HOST_PORT: LỖI (Không kết nối được)"
        status=1
    fi
    
    if nc -z -w 2 127.0.0.1 "$MOODLE_HOST_PORT" >/dev/null 2>&1; then
        echo "   - Cổng Moodle $MOODLE_HOST_PORT: OK (Đang lắng nghe)"
    else
        echo "   - Cổng Moodle $MOODLE_HOST_PORT: LỖI (Không kết nối được)"
        status=1
    fi
    
    if nc -z -w 2 127.0.0.1 "$JUPYTERHUB_HOST_PORT" >/dev/null 2>&1; then
        echo "   - Cổng JupyterHub $JUPYTERHUB_HOST_PORT: OK (Đang lắng nghe)"
    else
        echo "   - Cổng JupyterHub $JUPYTERHUB_HOST_PORT: LỖI (Không kết nối được)"
        status=1
    fi
else
    echo "   (Không có nc, dùng curl để kiểm tra HTTP endpoints)"
    moodle_http=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$MOODLE_HOST_PORT" || echo "000")
    if [ "$moodle_http" != "000" ]; then
        echo "   - Cổng Moodle $MOODLE_HOST_PORT: OK (HTTP Code: $moodle_http)"
    else
        echo "   - Cổng Moodle $MOODLE_HOST_PORT: LỖI (Không phản hồi HTTP)"
        status=1
    fi
    
    jhub_http=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$JUPYTERHUB_HOST_PORT/hub/health" || echo "000")
    if [ "$jhub_http" != "000" ]; then
        echo "   - Cổng JupyterHub $JUPYTERHUB_HOST_PORT: OK (HTTP Code: $jhub_http)"
    else
        echo "   - Cổng JupyterHub $JUPYTERHUB_HOST_PORT: LỖI (Không phản hồi HTTP)"
        status=1
    fi
fi

# 5. Kiểm tra quyền ghi của thư mục moodledata bằng user www-data
if [ -n "$moodle_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$moodle_status")" = "true" ]; then
    echo "5. Kiểm tra quyền ghi của thư mục moodledata:"
    
    # Kiểm tra root moodledata
    if docker compose exec -T moodle bash -lc "su -s /bin/sh www-data -c 'touch /var/moodledata/.write-test && rm /var/moodledata/.write-test'" >/dev/null 2>&1; then
        echo "   - Thư mục root /var/moodledata: OK (www-data có quyền ghi)"
    else
        echo "   - Thư mục root /var/moodledata: LỖI (www-data KHÔNG có quyền ghi!)"
        status=1
    fi
    
    # Kiểm tra cache directory default_application
    if docker compose exec -T moodle bash -lc "su -s /bin/sh www-data -c 'touch /var/moodledata/cache/cachestore_file/default_application/.write-test && rm /var/moodledata/cache/cachestore_file/default_application/.write-test'" >/dev/null 2>&1; then
        echo "   - Thư mục cache /var/moodledata/cache/...: OK (www-data có quyền ghi)"
    else
        echo "   - Thư mục cache /var/moodledata/cache/...: LỖI (www-data KHÔNG có quyền ghi!)"
        status=1
    fi
else
    echo "5. Kiểm tra quyền ghi moodledata: Bỏ qua vì Moodle container không chạy."
    status=1
fi

# 6. Kiểm tra cấu hình và tích hợp nbgrader
echo "6. Kiểm tra cấu hình và tích hợp nbgrader:"
if docker image inspect moodle-jupyter-singleuser:latest >/dev/null 2>&1; then
    # 6.1. Kiểm tra nbgrader version
    nb_version=$(docker run --rm moodle-jupyter-singleuser:latest nbgrader --version 2>/dev/null | tr -d '\r' | head -n 1 || echo "")
    if [ -n "$nb_version" ]; then
        echo "   - Lệnh nbgrader trong single-user image: OK ($nb_version)"
    else
        echo "   - Lệnh nbgrader trong single-user image: LỖI (Không tìm thấy hoặc không chạy được!)"
        status=1
    fi
    
    # 6.2. Kiểm tra quyền ghi của Giáo viên/Admin trên các volumes (exchange, courses, templates)
    teacher_write_check=$(docker run --rm \
      -v "${DATA_ROOT}/nbgrader/exchange:/srv/nbgrader/exchange" \
      -v "${DATA_ROOT}/nbgrader/courses:/srv/nbgrader/courses" \
      -v "${DATA_ROOT}/nbgrader/templates:/srv/nbgrader/templates" \
      moodle-jupyter-singleuser:latest \
      bash -c "
        touch /srv/nbgrader/exchange/.write-test && rm /srv/nbgrader/exchange/.write-test && \
        touch /srv/nbgrader/courses/.write-test && rm /srv/nbgrader/courses/.write-test && \
        touch /srv/nbgrader/templates/.write-test && rm /srv/nbgrader/templates/.write-test && \
        echo 'OK'
      " 2>/dev/null || echo "ERROR")
      
    if [ "$teacher_write_check" = "OK" ]; then
        echo "   - Quyền ghi của Giáo viên trên exchange, courses, templates: OK"
    else
        echo "   - Quyền ghi của Giáo viên trên exchange, courses, templates: LỖI (Giáo viên không có quyền ghi đầy đủ!)"
        status=1
    fi
    
    # 6.3. Kiểm tra bảo mật học sinh (không thấy courses và templates)
    student_courses_check=$(docker run --rm moodle-jupyter-singleuser:latest bash -c "ls -ld /srv/nbgrader/courses 2>&1" || echo "failed")
    student_templates_check=$(docker run --rm moodle-jupyter-singleuser:latest bash -c "ls -ld /srv/nbgrader/templates 2>&1" || echo "failed")
    
    if [[ "$student_courses_check" == *"No such file or directory"* ]] && [[ "$student_templates_check" == *"No such file or directory"* ]]; then
        echo "   - Kiểm tra bảo mật học sinh (không thấy courses và templates): OK"
    else
        echo "   - Kiểm tra bảo mật học sinh (không thấy courses và templates): LỖI (Thư mục bảo mật vẫn tồn tại hoặc truy cập được!)"
        echo "     + Thư mục courses: $student_courses_check"
        echo "     + Thư mục templates: $student_templates_check"
        status=1
    fi
    
    # 6.4. Kiểm tra xem có còn hardcode python101 trong global config không
    global_config_check=$(docker run --rm moodle-jupyter-singleuser:latest cat /etc/jupyter/nbgrader_config.py 2>/dev/null | grep "python101" || true)
    if [ -z "$global_config_check" ]; then
        echo "   - Kiểm tra global config (không chứa hardcode python101): OK"
    else
        echo "   - Kiểm tra global config (chứa hardcode python101!): LỖI"
        status=1
    fi
    
    # 6.5. Kiểm tra cấu hình và file mẫu demo mới
    demo_config_check=$(ls -la "${DATA_ROOT}/nbgrader/courses/course_demo/nbgrader_config.py" >/dev/null 2>&1 && echo "OK" || echo "MISSING")
    demo_template_check=$(ls -la "${DATA_ROOT}/nbgrader/templates/moodle_teacher_demo/python_basic/lab01_function/lab01_function.ipynb" >/dev/null 2>&1 && echo "OK" || echo "MISSING")
    
    if [ "$demo_config_check" = "OK" ] && [ "$demo_template_check" = "OK" ]; then
        echo "   - Kiểm tra cấu hình lớp học mẫu (course_demo và template): OK"
    else
        echo "   - Kiểm tra cấu hình lớp học mẫu: LỖI"
        echo "     + File config lớp học: $demo_config_check"
        echo "     + File template gốc: $demo_template_check"
        status=1
    fi
    
    # 6.6. Kiểm tra các container đang chạy của Giáo viên/Học sinh
    echo "   - Kiểm tra các container đang chạy của Giáo viên/Học sinh:"
    
    # Lấy danh sách các container teacher đang chạy
    teacher_containers=$(docker ps --format "{{.Names}}" | grep "teacher" || true)
    if [ -n "$teacher_containers" ]; then
        for tc in $teacher_containers; do
            echo "     + Đang kiểm tra container Giáo viên: $tc"
            # Kiểm tra xem các thư mục có tồn tại không
            tc_ex_exists=$(docker exec "$tc" [ -d /srv/nbgrader/exchange ] && echo "1" || echo "0")
            tc_co_exists=$(docker exec "$tc" [ -d /srv/nbgrader/courses ] && echo "1" || echo "0")
            tc_te_exists=$(docker exec "$tc" [ -d /srv/nbgrader/templates ] && echo "1" || echo "0")
            
            if [ "$tc_ex_exists" = "1" ] && [ "$tc_co_exists" = "1" ] && [ "$tc_te_exists" = "1" ]; then
                echo "       * Thư mục exchange, courses, templates tồn tại: OK"
            else
                echo "       * Thư mục exchange, courses, templates tồn tại: LỖI (Thiếu thư mục!)"
                echo "         FAIL: Teacher container does not mount nbgrader-courses/templates."
                status=1
            fi
            
            # Kiểm tra quyền ghi
            tc_ex_w=$(docker exec "$tc" [ -w /srv/nbgrader/exchange ] && echo "1" || echo "0")
            tc_co_w=$(docker exec "$tc" [ -w /srv/nbgrader/courses ] && echo "1" || echo "0")
            tc_te_w=$(docker exec "$tc" [ -w /srv/nbgrader/templates ] && echo "1" || echo "0")
            
            if [ "$tc_ex_w" = "1" ] && [ "$tc_co_w" = "1" ] && [ "$tc_te_w" = "1" ]; then
                echo "       * Quyền ghi trên exchange, courses, templates: OK"
            else
                echo "       * Quyền ghi trên exchange, courses, templates: LỖI (Không thể ghi!)"
                status=1
            fi
        done
    else
        echo "     + Không tìm thấy container Giáo viên đang chạy để kiểm tra."
    fi

    # Lấy danh sách các container student đang chạy
    student_containers=$(docker ps --format "{{.Names}}" | grep "student" || true)
    if [ -n "$student_containers" ]; then
        for sc in $student_containers; do
            echo "     + Đang kiểm tra container Học sinh: $sc"
            # Kiểm tra xem exchange có tồn tại không, và courses/templates không được phép tồn tại
            sc_ex_exists=$(docker exec "$sc" [ -d /srv/nbgrader/exchange ] && echo "1" || echo "0")
            sc_co_exists=$(docker exec "$sc" [ -d /srv/nbgrader/courses ] && echo "1" || echo "0")
            sc_te_exists=$(docker exec "$sc" [ -d /srv/nbgrader/templates ] && echo "1" || echo "0")
            
            if [ "$sc_ex_exists" = "1" ]; then
                echo "       * Thư mục exchange tồn tại: OK"
            else
                echo "       * Thư mục exchange tồn tại: LỖI (Thiếu exchange!)"
                status=1
            fi
            
            if [ "$sc_co_exists" = "0" ] && [ "$sc_te_exists" = "0" ]; then
                echo "       * Bảo mật (courses/templates không tồn tại): OK"
            else
                echo "       * Bảo mật (courses/templates không tồn tại): LỖI (Học sinh có thể truy cập courses/templates!)"
                echo "         FAIL: Student container has access to courses or templates."
                status=1
            fi
        done
    else
        echo "     + Không tìm thấy container Học sinh đang chạy để kiểm tra."
    fi
else
    echo "   - Kiểm tra nbgrader: Bỏ qua vì single-user image chưa được build."
    status=1
fi

# 10. Kiểm tra jupyter-assignment-service
echo "10. Kiểm tra jupyter-assignment-service:"
if [ -n "$assignment_status" ] && [ "$(docker inspect -f '{{.State.Running}}' "$assignment_status")" = "true" ]; then
    echo "   - Container: OK (Đang chạy)"
    
    # Kiểm tra quyền truy cập Docker socket (Docker CLI)
    docker_ps_err=$(docker compose exec -T jupyter-assignment-service sh -c "docker ps 2>&1" || echo "non-zero exit")
    if [[ "$docker_ps_err" != *"error"* && "$docker_ps_err" != *"Cannot connect"* && "$docker_ps_err" != *"Permission denied"* && "$docker_ps_err" != *"non-zero exit"* ]]; then
        echo "   - Quyền truy cập Docker socket (CLI): OK"
    else
        echo "   - Quyền truy cập Docker socket (CLI): Bỏ qua (Đã chuyển sang dùng Python Docker SDK trực tiếp để tối ưu)"
    fi

    # Kiểm tra Docker SDK Python
    docker_sdk_err=$(docker compose exec -T jupyter-assignment-service python3 -c "import docker; c=docker.from_env(); print('OK' if c.ping() else 'FAIL')" 2>&1 || echo "non-zero exit")
    if [ "$docker_sdk_err" = "OK" ]; then
        echo "   - Quyền truy cập Docker socket (Python SDK): OK"
    else
        echo "   - Quyền truy cập Docker socket (Python SDK): LỖI ($docker_sdk_err)"
        status=1
    fi
    
    # Kiểm tra database PostgreSQL của service
    pg_conn_check=$(docker compose exec -T jupyter-assignment-service python3 -c "from main import engine; conn=engine.connect(); print('OK')" 2>/dev/null || echo "ERROR")
    if [ "$pg_conn_check" = "OK" ] ; then
        echo "   - Kết nối Database PostgreSQL (assignment_service): OK"
    else
        echo "   - Kết nối Database PostgreSQL (assignment_service): LỖI (Không kết nối được: $pg_conn_check)"
        status=1
    fi
    
    # Kiểm tra HTTP endpoint qua proxy của JupyterHub
    service_http=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$JUPYTERHUB_HOST_PORT/services/assignment-service/gui" || echo "000")
    if [ "$service_http" = "401" ] || [ "$service_http" = "200" ] || [ "$service_http" = "302" ]; then
        echo "   - Kết nối HTTP Endpoint qua proxy (HTTP Code: $service_http): OK"
    else
        echo "   - Kết nối HTTP Endpoint qua proxy (HTTP Code: $service_http): LỖI"
        status=1
    fi
else
    echo "   - Container: LỖI (Không hoạt động)"
    status=1
fi

echo "========================================================="
if [ $status -eq 0 ]; then
    echo " KẾT LUẬN: Hệ thống Moodle + JupyterHub hoạt động tuyệt vời!"
else
    echo " KẾT LUẬN: Có lỗi phát hiện trong hệ thống. Vui lòng kiểm tra log chi tiết."
fi
exit $status
