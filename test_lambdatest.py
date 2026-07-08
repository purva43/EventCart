from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

# 🔐 Credentials (⚠️ regenerate your key!)
username = "vinaytembare45"
access_key = "LT_RyGEg41Qm8KKaKJCIdDSp7YMkm6KXQXcTkgVkfwpUDPlnMH"

hub_url = f"https://{username}:{access_key}@hub.lambdatest.com/wd/hub"

# 🌐 Options
options = Options()
options.set_capability("browserName", "Chrome")
options.set_capability("browserVersion", "latest")

options.set_capability("LT:Options", {
    "platformName": "Windows 10",
    "build": "Event Project Test",
    "name": "Full User Flow Test",

    # ✅ FIXED (comma added)
    "tunnel": True,

    # ✅ Enable logs
    "console": True,
    "network": True,
    "devicelog": True,
    "terminal": True,
    "visual": True
})

# 🚀 Driver
driver = webdriver.Remote(
    command_executor=hub_url,
    options=options
)

wait = WebDriverWait(driver, 20)

try:
    # 🌍 Open app
    driver.get("http://localhost:8000")

    # =========================
    # 📝 STEP 1: CLICK REGISTER
    # =========================
    register_btn = wait.until(EC.element_to_be_clickable((
        By.XPATH, "//a[contains(text(),'Register')]"
    )))
    register_btn.click()

    print("➡️ Navigated to:", driver.current_url)
    driver.save_screenshot("step_register.png")

    # =========================
    # 🧾 STEP 2: REGISTER
    # =========================
    wait.until(EC.url_contains("register"))

    username_input = wait.until(EC.visibility_of_element_located((By.NAME, "username")))
    username_input.send_keys("purva")

    email_input = wait.until(EC.visibility_of_element_located((By.NAME, "email")))
    email_input.send_keys("avatar297@gmail.com")

    password_input = wait.until(EC.visibility_of_element_located((By.NAME, "password")))
    password_input.send_keys("StrongPass123")  # ✅ FIXED

    submit_btn = wait.until(EC.element_to_be_clickable((
        By.XPATH, "//form//button[@type='submit']"
    )))
    submit_btn.click()

    print("✅ Registration submitted")

    # =========================
    # 🔍 STEP 3: SEARCH
    # =========================
    search_box = wait.until(EC.visibility_of_element_located((By.NAME, "q")))
    search_box.clear()
    search_box.send_keys("conference")

    search_btn = wait.until(EC.element_to_be_clickable((
        By.XPATH, "//form//button[@type='submit']"
    )))
    search_btn.click()

    print("✅ Search done")

    # =========================
    # 👉 STEP 4: CLICK RESULT
    # =========================
    first_result = wait.until(EC.element_to_be_clickable((
        By.XPATH, "(//div[contains(@class,'card')]//a)[1]"
    )))
    first_result.click()

    print("✅ Opened first result")

    # =========================
    # 🔗 STEP 5: CLICK EVENT LINK
    # =========================
    event_link = wait.until(EC.element_to_be_clickable((
        By.XPATH, "//a[contains(@class,'btn') or @target='_blank']"
    )))
    event_link.click()

    print("✅ Event link opened")

    print("🎉 TEST PASSED")

except Exception as e:
    print("❌ TEST FAILED:", e)
    driver.save_screenshot("error.png")

finally:
    driver.quit()