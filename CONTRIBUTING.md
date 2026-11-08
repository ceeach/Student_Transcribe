# Contributing to Student Transcribe

Thank you for your interest in contributing to **Student Transcribe**! We welcome bug reports, feature requests, documentation improvements, and code contributions.

---

## 🛠️ Development Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/ceeach/Student_Transcribe.git
   cd Student_Transcribe
   ```

2. **Create a Virtual Environment**:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Application**:
   ```bash
   python main.py
   ```

---

## 📦 Building the Installer (Windows)

To bundle the application into a standalone portable folder and Inno Setup installer:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1
```
The compiled installer will be in `output/` and the standalone distribution in `dist/Student Transcribe/`.

---

## 🤝 How to Contribute

1. **Fork the repository** on GitHub.
2. **Create a feature branch**:
   ```bash
   git checkout -b feature/my-new-feature
   ```
3. **Commit your changes** with descriptive commit messages:
   ```bash
   git commit -m "Add new audio filter setting"
   ```
4. **Push to your fork**:
   ```bash
   git push origin feature/my-new-feature
   ```
5. **Open a Pull Request** against the `master` branch.

---

## 📋 Reporting Issues & Requesting Features
- Please use GitHub Issues to report bugs or request features.
- When filing a bug, include:
  - Your OS version.
  - Steps to reproduce the problem.
  - Relevant lines from the application log (`Settings → Diagnostics & Application Logs → View Application Logs`).
