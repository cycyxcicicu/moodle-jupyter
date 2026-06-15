<?php
/**
 * CLI script to configure JupyterHub LTI 1.3 External Tool inside Moodle.
 * Chạy trong context của Moodle để tạo/cập nhật cấu hình LTI 1.3 của JupyterHub một cách idempotent.
 */

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/mod/lti/locallib.php');

global $DB, $CFG;

echo "=== Đang chạy kịch bản đăng ký/cập nhật JupyterHub LTI 1.3 ===\n";

// 1. Kiểm tra trạng thái cài đặt của Moodle
if (!$DB->get_manager()->table_exists('config')) {
    echo "LỖI: Moodle chưa được cài đặt cơ sở dữ liệu. Vui lòng cài đặt trước.\n";
    exit(1);
}

// Cho phép nhúng iframe nếu sau này cần thiết (bật config hệ thống)
set_config('allowframeembedding', 1);

// Tắt debug display trong database để đồng bộ với config.php
set_config('debug', 0);
set_config('debugdisplay', 0);

/**
 * Class quản lý thao tác trực tiếp với cơ sở dữ liệu của Moodle.
 * Lý do phải thao tác trực tiếp qua DB ($DB object):
 * - Trong môi trường PHP CLI tự động (entrypoint/startup script), các thư viện giao diện người dùng 
 *   và session context của Moodle (như mod/lti/locallib.php) có thể kiểm tra quyền quản trị viên 
 *   của session hiện tại và ném ra các lỗi chuyển hướng (redirect) hoặc lỗi phân quyền không đáng có.
 * - Thao tác trực tiếp qua $DB đảm bảo script chạy mượt mà, không phụ thuộc vào trạng thái đăng nhập,
 *   đồng thời dễ dàng kiểm soát tính idempotent và bảo lưu clientid cũ của LTI Tool.
 */
class JupyterLtiDbManager {
    private $DB;
    private $tool_name = 'JupyterHub';

    public function __construct($DB) {
        $this->DB = $DB;
    }

    /**
     * Dọn dẹp tool trùng lặp nếu có để tránh lỗi truy vấn.
     */
    public function cleanup_duplicate_tools() {
        try {
            $this->DB->execute("
                DELETE FROM {lti_types}
                WHERE name = ? AND id NOT IN (
                    SELECT MIN(id)
                    FROM {lti_types}
                    WHERE name = ?
                )
            ", array($this->tool_name, $this->tool_name));
        } catch (Throwable $e) {
            echo "Cảnh báo khi dọn dẹp tool trùng lặp: " . $e->getMessage() . "\n";
        }
    }

    /**
     * Dọn dẹp cấu hình trùng lặp nếu có để tránh lỗi truy vấn.
     */
    public function cleanup_duplicate_configs() {
        try {
            // Xóa cấu hình mồ côi
            $this->DB->execute("
                DELETE FROM {lti_types_config}
                WHERE typeid NOT IN (SELECT id FROM {lti_types})
            ");
            
            // Xóa các bản ghi cấu hình trùng lặp (chỉ giữ lại bản ghi có ID nhỏ nhất cho mỗi typeid + name)
            $this->DB->execute("
                DELETE FROM {lti_types_config} 
                WHERE id NOT IN (
                    SELECT MIN(id) 
                    FROM {lti_types_config} 
                    GROUP BY typeid, name
                )
            ");
        } catch (Throwable $e) {
            echo "Cảnh báo khi dọn dẹp cấu hình trùng lặp: " . $e->getMessage() . "\n";
        }
    }

    /**
     * Dọn dẹp các hoạt động LTI cũ bị cấu hình sai URL.
     */
    public function cleanup_bad_activities() {
        try {
            // Xóa các hoạt động LTI có toolurl chứa 'oauth_callback'
            $this->DB->execute("
                DELETE FROM {lti}
                WHERE toolurl LIKE '%oauth_callback%'
            ");
            echo "Đã dọn dẹp các hoạt động LTI cũ bị lỗi URL chứa oauth_callback.\n";
        } catch (Throwable $e) {
            echo "Cảnh báo khi dọn dẹp hoạt động LTI: " . $e->getMessage() . "\n";
        }
    }

    /**
     * Tìm kiếm LTI tool hiện tại theo tên.
     */
    public function find_existing_tool() {
        return $this->DB->get_record('lti_types', array('name' => $this->tool_name));
    }

    /**
     * Đăng ký mới hoặc Cập nhật cấu hình của LTI Tool.
     * Bảo lưu clientid cũ nếu đã tồn tại để tránh phá vỡ kết nối hiện tại.
     */
    public function configure_tool($jupyterhub_url) {
        $this->cleanup_duplicate_tools();
        $this->cleanup_duplicate_configs();
        $this->cleanup_bad_activities();
        
        $existing = $this->find_existing_tool();
        $admin_id = $this->DB->get_field('user', 'id', array('username' => 'admin')) ?: 2;
        $course_visible = 2; // Hiển thị trong bộ chọn hoạt động và làm công cụ cấu hình sẵn.

        // Chuẩn bị URL callback và launch URL đúng chuẩn LTI 1.3
        $toolurl = rtrim($jupyterhub_url, '/');
        $initiatelogin = rtrim($jupyterhub_url, '/') . '/hub/lti13/oauth_login';
        $callbackurl = rtrim($jupyterhub_url, '/') . '/hub/lti13/oauth_callback';
        $redirectionuris = $callbackurl;

        if ($existing) {
            echo "Tìm thấy LTI Tool '$this->tool_name' đã tồn tại. Bảo lưu Client ID cũ: " . $existing->clientid . "\n";
            
            // Cập nhật lti_types
            $existing->baseurl = $toolurl;
            $existing->tooldomain = parse_url($jupyterhub_url, PHP_URL_HOST);
            $existing->ltiversion = '1.3.0';
            $existing->coursevisible = $course_visible;
            $existing->timemodified = time();
            $this->DB->update_record('lti_types', $existing);
            $typeid = $existing->id;
            $client_id = $existing->clientid;
        } else {
            // Tạo mới hoàn toàn
            // Moodle LTI 1.3 Client ID là một chuỗi băm ngẫu nhiên. Ta tự sinh ở đây để đảm bảo cấu trúc.
            $client_id = 'jupyterhub-lti-' . bin2hex(random_bytes(8));
            echo "Không tìm thấy LTI Tool. Tạo mới LTI Tool '$this->tool_name' với Client ID mới: " . $client_id . "\n";

            $type = new stdClass();
            $type->name = $this->tool_name;
            $type->baseurl = $toolurl;
            $type->tooldomain = parse_url($jupyterhub_url, PHP_URL_HOST);
            $type->state = 1; // Hoạt động (Configured)
            $type->ltiversion = '1.3.0';
            $type->clientid = $client_id;
            $type->course = 1; // Toàn hệ thống (Site tool)
            $type->coursevisible = $course_visible;
            $type->createdby = $admin_id;
            $type->timecreated = time();
            $type->timemodified = time();

            $typeid = $this->DB->insert_record('lti_types', $type);
        }

        // Xác định giá trị launchcontainer từ hằng số Moodle hoặc fallback là 2 (Embed)
        $launchcontainer = defined('LTI_LAUNCH_CONTAINER_EMBED') ? LTI_LAUNCH_CONTAINER_EMBED : 2;

        // Xóa tất cả các key cấu hình cũ có tiền tố lti_ để đảm bảo DB sạch
        $this->DB->execute("DELETE FROM {lti_types_config} WHERE typeid = ? AND name LIKE 'lti_%'", array($typeid));

        // Xóa các cấu hình cũ của Tool này ngoài các key ta chuẩn bị cập nhật để tránh rác
        $keys_str = "'toolurl','initiatelogin','redirectionuris','keytype','publickeyset','sendname','sendemail','sendemailaddr','acceptgrades','launchcontainer'";
        $this->DB->execute("DELETE FROM {lti_types_config} WHERE typeid = ? AND name NOT IN ($keys_str)", array($typeid));

        // Cập nhật cấu hình chi tiết cho LTI 1.3
        $configs = array(
            'toolurl' => $toolurl,
            'initiatelogin' => $initiatelogin,
            'redirectionuris' => $redirectionuris,
            'keytype' => 'JWK_KEYSET',
            // URL JWKS của JupyterHub để Moodle có thể kiểm tra chữ ký (gọi qua Docker network nội bộ)
            'publickeyset' => 'http://jupyterhub:8000/hub/lti13/jwks',
            'sendname' => '1',      // 1 = Luôn gửi tên (LTI_SETTING_ALWAYS)
            'sendemail' => '1',     // Cấu hình dự phòng
            'sendemailaddr' => '1', // 1 = Luôn gửi địa chỉ email (LTI_SETTING_ALWAYS)
            'acceptgrades' => '1',  // 1 = Luôn chấp nhận điểm số (LTI_SETTING_ALWAYS)
            'launchcontainer' => (string)$launchcontainer
        );

        foreach ($configs as $name => $value) {
            // Đảm bảo không tạo duplicate bằng cách update nếu đã tồn tại, ngược lại thì insert
            $existing_config = $this->DB->get_record('lti_types_config', array('typeid' => $typeid, 'name' => $name));
            if ($existing_config) {
                $existing_config->value = $value;
                $this->DB->update_record('lti_types_config', $existing_config);
            } else {
                $config = new stdClass();
                $config->typeid = $typeid;
                $config->name = $name;
                $config->value = $value;
                $this->DB->insert_record('lti_types_config', $config);
            }
        }

        // Cập nhật toàn bộ thực thể hoạt động LTI hiện tại của JupyterHub sang launchcontainer
        $this->DB->execute("UPDATE {lti} SET launchcontainer = ? WHERE typeid = ?", array($launchcontainer, $typeid));

        return (object)[
            'client_id' => $client_id,
            'type_id' => $typeid
        ];
    }
}

// Lấy thông tin JupyterHub public URL từ môi trường
$jupyterhub_url = getenv('JUPYTERHUB_URL') ?: 'http://localhost:18000';

$manager = new JupyterLtiDbManager($DB);
$tool_info = $manager->configure_tool($jupyterhub_url);

// Xóa sạch cache Moodle để cấu hình có hiệu lực lập tức
echo "Đang xóa toàn bộ cache của Moodle...\n";
purge_all_caches();

echo "Cấu hình LTI Tool thành công! Thông tin LTI của Moodle:\n";

// In ra cấu hình LTI ở định dạng KEY=VALUE giữa các dòng phân định để bash script dễ parse
echo "--- LTI CONFIG START ---\n";
echo "MOODLE_ISSUER=" . $CFG->wwwroot . "\n";
echo "MOODLE_CLIENT_ID=" . $tool_info->client_id . "\n";
echo "MOODLE_AUTHORIZE_URL=" . $CFG->wwwroot . "/mod/lti/auth.php\n";
echo "MOODLE_JWKS_URL=http://moodle/mod/lti/certs.php\n";
echo "--- LTI CONFIG END ---\n";

exit(0);
