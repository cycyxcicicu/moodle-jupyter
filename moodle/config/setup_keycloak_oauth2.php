<?php
/**
 * CLI script to configure Keycloak OAuth2 Issuer inside Moodle.
 * Chạy trong context của Moodle để tạo/cập nhật cấu hình OAuth2 của Keycloak một cách idempotent.
 */

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');

global $DB, $CFG;

echo "=== Đang chạy kịch bản cấu hình Keycloak OAuth2 SSO ===\n";

// 1. Kiểm tra trạng thái cài đặt của Moodle
if (!$DB->get_manager()->table_exists('oauth2_issuer')) {
    echo "LỖI: Moodle chưa được cài đặt cơ sở dữ liệu hoặc thiếu bảng oauth2_issuer.\n";
    exit(1);
}

// 2. Load cấu hình từ Environment variables
$keycloak_issuer_url = getenv('KEYCLOAK_ISSUER') ?: 'http://keycloak.school.local:18090/realms/school';
$moodle_client_id = getenv('MOODLE_OIDC_CLIENT_ID') ?: 'moodle-client';
$moodle_client_secret = getenv('MOODLE_OIDC_CLIENT_SECRET') ?: 'moodle-secret-123';

echo "Cấu hình OIDC: \n";
echo " - Issuer URL: {$keycloak_issuer_url}\n";
echo " - Client ID: {$moodle_client_id}\n";
echo " - Client Secret: " . substr($moodle_client_secret, 0, 5) . "...\n";

// 3. Khởi tạo hoặc cập nhật Issuer
$issuer_name = 'Keycloak';
$existing_issuer = $DB->get_record('oauth2_issuer', array('name' => $issuer_name));

$issuer_data = new stdClass();
$issuer_data->name = $issuer_name;
$issuer_data->image = '';
$issuer_data->clientid = $moodle_client_id;
$issuer_data->clientsecret = $moodle_client_secret;
$issuer_data->baseurl = $keycloak_issuer_url;
$issuer_data->enabled = 1;
$issuer_data->showonloginpage = 1; // Hiển thị nút đăng nhập trên màn hình login
$issuer_data->requireconfirmation = 0; // Đăng nhập lập tức không cần xác nhận email lại
$issuer_data->servicetype = 'custom';
$issuer_data->timemodified = time();
$issuer_data->usermodified = 2; // Thường là user admin mặc định (id = 2)

// Các trường bắt buộc bổ sung cho Moodle 4.x để tránh lỗi constraint NOT NULL
$issuer_data->loginscopes = 'openid profile email';
$issuer_data->loginscopesoffline = 'openid profile email';
$issuer_data->basicauth = 1;
$issuer_data->loginparams = '';
$issuer_data->loginparamsoffline = '';
$issuer_data->alloweddomains = '';
$issuer_data->scopessupported = '';
$issuer_data->sortorder = 0;

try {
    if ($existing_issuer) {
        echo "Phát hiện Issuer 'Keycloak' đã tồn tại. Đang cập nhật...\n";
        $issuer_data->id = $existing_issuer->id;
        $DB->update_record('oauth2_issuer', $issuer_data);
        $issuer_id = $existing_issuer->id;
    } else {
        echo "Tạo mới Issuer 'Keycloak'...\n";
        $issuer_data->timecreated = time();
        $issuer_id = $DB->insert_record('oauth2_issuer', $issuer_data);
    }
} catch (Exception $e) {
    echo "❌ Lỗi cơ sở dữ liệu khi lưu Issuer:\n";
    echo $e->getMessage() . "\n";
    if (isset($e->debuginfo)) {
        echo "Chi tiết SQL: " . $e->debuginfo . "\n";
    }
    exit(1);
}

echo "Issuer ID: {$issuer_id}\n";

// 4. Cấu hình các Endpoints cho Keycloak Issuer
$endpoints = array(
    'authorization_endpoint' => $keycloak_issuer_url . '/protocol/openid-connect/auth',
    'token_endpoint' => $keycloak_issuer_url . '/protocol/openid-connect/token',
    'userinfo_endpoint' => $keycloak_issuer_url . '/protocol/openid-connect/userinfo',
    'jwks_uri' => $keycloak_issuer_url . '/protocol/openid-connect/certs',
    'end_session_endpoint' => $keycloak_issuer_url . '/protocol/openid-connect/logout'
);

foreach ($endpoints as $name => $url) {
    $existing_endpoint = $DB->get_record('oauth2_endpoint', array('issuerid' => $issuer_id, 'name' => $name));
    
    $endpoint_data = new stdClass();
    $endpoint_data->issuerid = $issuer_id;
    $endpoint_data->name = $name;
    $endpoint_data->url = $url;
    $endpoint_data->timemodified = time();
    $endpoint_data->usermodified = 2;
    
    if ($existing_endpoint) {
        $endpoint_data->id = $existing_endpoint->id;
        $DB->update_record('oauth2_endpoint', $endpoint_data);
        echo " - Đã cập nhật endpoint: {$name} -> {$url}\n";
    } else {
        $endpoint_data->timecreated = time();
        $DB->insert_record('oauth2_endpoint', $endpoint_data);
        echo " - Đã thêm mới endpoint: {$name} -> {$url}\n";
    }
}

// 5. Cấu hình Mappings trường thông tin người dùng
$mappings = array(
    'preferred_username' => 'username',
    'email' => 'email',
    'given_name' => 'firstname',
    'family_name' => 'lastname'
);

foreach ($mappings as $external => $internal) {
    $existing_mapping = $DB->get_record('oauth2_user_field_mapping', array('issuerid' => $issuer_id, 'internalfield' => $internal));
    
    $mapping_data = new stdClass();
    $mapping_data->issuerid = $issuer_id;
    $mapping_data->externalfield = $external;
    $mapping_data->internalfield = $internal;
    $mapping_data->timemodified = time();
    $mapping_data->usermodified = 2;
    
    if ($existing_mapping) {
        $mapping_data->id = $existing_mapping->id;
        $DB->update_record('oauth2_user_field_mapping', $mapping_data);
        echo " - Đã cập nhật map: {$external} -> {$internal}\n";
    } else {
        $mapping_data->timecreated = time();
        $DB->insert_record('oauth2_user_field_mapping', $mapping_data);
        echo " - Đã thêm mới map: {$external} -> {$internal}\n";
    }
}

// 6. Kích hoạt plugin auth_oauth2 trong Moodle
$enabled_auths = get_config(null, 'auth');
if (empty($enabled_auths)) {
    $enabled_auths = 'manual,nologin';
}
$auths = explode(',', $enabled_auths);
if (!in_array('oauth2', $auths)) {
    $auths[] = 'oauth2';
    set_config('auth', implode(',', $auths));
    echo "Đã kích hoạt plugin auth_oauth2 trong danh sách plugin xác thực Moodle.\n";
} else {
    echo "Plugin auth_oauth2 đã được kích hoạt từ trước.\n";
}

// 7. Vô hiệu hóa bộ lọc cURL Blocked Hosts & Ports trong môi trường phát triển (Development)
// Điều này giúp Moodle kết nối tự do tới các container nội bộ khác (Keycloak, GitLab, JupyterHub...)
set_config('curlsecurityblockedhosts', '');
set_config('curlsecurityallowedport', '');
echo " - Đã vô hiệu hóa cURL Blocked Hosts & Allowed Ports để cho phép gọi nội bộ.\n";

// Xóa cache để cập nhật các thay đổi
purge_all_caches();
echo "Purged all Moodle caches.\n";

// TEST KẾT NỐI BACKCHANNEL SANG KEYCLOAK TOKEN ENDPOINT
$test_url = $keycloak_issuer_url . '/protocol/openid-connect/token';
echo "--- ĐANG KIỂM TRA KẾT NỐI ĐƯỜNG TRUYỀN BACKCHANNEL SANG TOKEN ENDPOINT ---\n";
echo "URL: {$test_url}\n";

// --- TEST 1: client_secret_post (Gửi client_id & client_secret trong POST Body) ---
echo "\n--- TEST 1: client_secret_post ---\n";
$ch1 = curl_init($test_url);
curl_setopt($ch1, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch1, CURLOPT_POST, true);
curl_setopt($ch1, CURLOPT_POSTFIELDS, http_build_query([
    'grant_type' => 'authorization_code',
    'code' => 'test-code-dummy',
    'redirect_uri' => 'http://moodle.school.local:18080/admin/oauth2callback.php',
    'client_id' => $moodle_client_id,
    'client_secret' => $moodle_client_secret
]));
curl_setopt($ch1, CURLOPT_SSL_VERIFYPEER, false);
curl_setopt($ch1, CURLOPT_SSL_VERIFYHOST, false);
curl_setopt($ch1, CURLOPT_TIMEOUT, 10);
$response1 = curl_exec($ch1);
$http_code1 = curl_getinfo($ch1, CURLINFO_HTTP_CODE);
$error1 = curl_error($ch1);
curl_close($ch1);

echo "HTTP Status Code (POST): {$http_code1}\n";
if ($error1) {
    echo "Lỗi curl (POST): {$error1}\n";
} else {
    echo "Phản hồi từ Keycloak (POST): {$response1}\n";
}

// --- TEST 2: client_secret_basic (Gửi credentials qua HTTP Basic Auth Header) ---
echo "\n--- TEST 2: client_secret_basic ---\n";
$ch2 = curl_init($test_url);
curl_setopt($ch2, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch2, CURLOPT_POST, true);
curl_setopt($ch2, CURLOPT_USERPWD, "{$moodle_client_id}:{$moodle_client_secret}");
curl_setopt($ch2, CURLOPT_POSTFIELDS, http_build_query([
    'grant_type' => 'authorization_code',
    'code' => 'test-code-dummy',
    'redirect_uri' => 'http://moodle.school.local:18080/admin/oauth2callback.php'
]));
curl_setopt($ch2, CURLOPT_SSL_VERIFYPEER, false);
curl_setopt($ch2, CURLOPT_SSL_VERIFYHOST, false);
curl_setopt($ch2, CURLOPT_TIMEOUT, 10);
$response2 = curl_exec($ch2);
$http_code2 = curl_getinfo($ch2, CURLINFO_HTTP_CODE);
$error2 = curl_error($ch2);
curl_close($ch2);

echo "HTTP Status Code (BASIC): {$http_code2}\n";
if ($error2) {
    echo "Lỗi curl (BASIC): {$error2}\n";
} else {
    echo "Phản hồi từ Keycloak (BASIC): {$response2}\n";
}
echo "-----------------------------------------------------------------------\n";

echo "=== Đã hoàn thành cấu hình Keycloak OAuth2 thành công! ===\n";
