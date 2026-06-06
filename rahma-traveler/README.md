# Rahma Traveler Flask App

A robust CRM and booking management system designed for Rahma Traveler, featuring automated data imports, identity resolution, and real-time handoff queues.

## Setup Instructions

Follow these steps to get the development environment running:

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Initialize Database**
   ```bash
   flask db init
   flask db migrate
   flask db upgrade
   ```

3. **Import Excel Data**
   Ensure your Excel file is placed at the path specified in `.env` (default: `data/travelers_database.xlsx`).
   ```bash
   python scripts/import_excel.py
   ```

4. **Run the Application**
   ```bash
   flask run
   ```

## Configuration (.env.example)

Create a `.env` file in the root directory and configure the following variables:

```text
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
DATABASE_URL=sqlite:///rahma.db
EXCEL_FILE_PATH=data/travelers_database.xlsx
```

## Dependencies (requirements.txt)

The project relies on the following core libraries:

- **Flask (3.0.0)**: Web framework.
- **SQLAlchemy (3.1.0)**: ORM for database management.
- **Migrate (4.0.5)**: Database migrations.
- **Login (0.6.3)**: User authentication.
- **SocketIO (5.3.6)**: Real-time WebSocket communication.
- **Pandas (2.1.0) & Openpyxl (3.1.2)**: Excel data processing.
- **Dotenv (1.0.0)**: Environment variable management.
- **Phonenumbers (8.13.0)**: Phone number normalization.

## API Endpoints Summary

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/travelers/` | GET | List all travelers with filtering and pagination. |
| `/travelers/` | POST | Create a new traveler record. |
| `/travelers/<id>` | GET | View detailed traveler profile. |
| `/travelers/<id>` | PUT | Update traveler information. |
| `/travelers/export` | GET | Export traveler list to CSV. |
| `/admin/handoffs/` | GET | Kanban board for AI-to-human handoffs. |
| `/admin/handoffs/` | POST | Create a new handoff request (triggers alert). |
| `/admin/handoffs/pending`| GET | Returns the count of pending handoffs. |
| `/api/copy/render` | POST | Deterministic template rendering (Copy Guard). |
| `/api/copy/templates` | GET | List all available message templates. |
| `/api/crm/duplicates` | GET | Identify potential duplicate travelers. |
| `/api/crm/resolve-identity`| POST | Merge duplicate records into a master profile. |
