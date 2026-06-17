import subprocess
import sys

try:
    result = subprocess.run([sys.executable, 'scratch/check_trips.py'], capture_output=True, text=True)
    with open('scratch/trips_output.txt', 'w') as f:
        f.write("STDOUT:\n")
        f.write(result.stdout)
        f.write("\nSTDERR:\n")
        f.write(result.stderr)
    print("Successfully ran and wrote output")
except Exception as e:
    with open('scratch/trips_output.txt', 'w') as f:
        f.write(str(e))
