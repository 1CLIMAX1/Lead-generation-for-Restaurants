from flask import Flask, render_template
from flask_mysqldb import MySQL

app = Flask(__name__)

# 1. Configure your MySQL connection details
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'         # Your MySQL username
app.config['MYSQL_PASSWORD'] = '@Urlosing12' # Your MySQL password
app.config['MYSQL_DB'] = 'lead_generation'  # Your database name

mysql = MySQL(app)

# 2. Create a route for your home page
@app.get('/')
def home():
    # Connect to the database
    cursor = mysql.connection.cursor()
    
    # Run the SQL query to grab data from your ready table
    # CHANGE 'your_table_name' to match your actual table name!
    cursor.execute("SELECT * FROM restaurant_leads_with_phone")
    
    # Fetch all rows from the query result
    database_data = cursor.fetchall()
    
    # Close the connection
    cursor.close()
    
    # Send the data over to the HTML file to be displayed
    return render_template('index.html', data=database_data)

if __name__ == '__main__':
    # Start the local development server
    app.run(debug=True)
