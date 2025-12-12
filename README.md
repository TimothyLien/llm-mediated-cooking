# **LLM-Mediated Task Planner**
A large language model–mediated task planner for human–robot collaboration (HRC) in a text-based cooking game environment.

---

## **Installation**
Clone the repository **with submodules**, as the project depends on the `downward` PDDL planner:

```
git clone --recurse-submodules git@github.com:TimothyLien/llm-mediated-task-planner.git
cd ll-mediated-task-planner
```

## **Environment**
Create a python environment and activate it
```
python3 -m venv env

#macOS/Linus
source env/bin/activate

#Windows/Powershell
env\Scripts\Activate.ps1
```

Install necessary packages with
```
pip install -r requirements.txt
```

Create a .env file in the root directory and add these lines
```
GEMINI_API_KEY="Insert Api Key Here"
GROQ_API_KEY='Insert Api Key Here'
```

## Build the pddl solver (downward)
Change the current directory to the downwards subfolder
```
cd downward
```

Now, run the build script
```
./build.py
```

You can test if the solver works by running the following command
```
./fast-downward.py domain.pddl problem.pddl --search "astar(lmcut())"
```

## Running the program
Return to the root directory with
```
cd ..
```
and run 
```
python3 main.py
```
to launch the program. After the conversation ends, run 
```
python3 app.py
```
and visit the local host link shown in the console.