> **Deprecated:** This document describes the original pipeline architecture from the 2024 LLM Zoomcamp submission (Mage, MLflow, Google Drive). It is kept for historical reference only and does not reflect the current application. For current setup, see the [Quick Start](../README.md#quick-start-docker) in the README.

# Setup and requirements (historical)
Project requires Docker and docker-compose to start.

Data loading pipeline **will use anonymized data from my dives if the credentials file and folder_id are not available**. This is to ensure that the pipeline can be run without any issues.

There is also an option to run batch inference pipeline and use async requests to export resulted data to a Notion page. This requires a Notion token and a database id. **If they are not provided - the pipeline will only print the data to the console.**


## How to run the project

Assuming you already started the Codespace, and finished docker-compose procedures, now we can start running the project.

The project is split into 4 main pipelines:

1. Dives data loading pipeline - loads and parses dives data from Subsurface logbook
2. DAN RAG pipeline - loads and vectorizes DAN content, and evaluates RAG prompts
3. Training pipeline - trains the model on the dives data and saves the best model to the MLflow registry
4. Report generation pipeline - loads the model from the MLflow registry, predicts the rating for the dives, generates reports on each dive, augments reports with insights using vectorized DAN content, and sends final reports to Notion

<img width="1134" alt="Screenshot 2024-09-14 at 10 54 53 AM" src="https://github.com/user-attachments/assets/86e40f78-53fe-404b-8ca4-1b47d9a6e3ac">


Project is using [Mage](https://magefile.org/) to manage the pipelines and [MLflow](https://mlflow.org/) to manage the models and to monitor experiments and RAG prompts evaluation.

### Dive data loading pipeline

Loads and parses dives data from Subsurface logbook. If the credentials file and folder_id are not available, the pipeline will use anonymized data from my dives.

### DAN RAG pipeline

_If you are here to evaluate this project for LLM zoomcamp - this is the only pipeline you need to run._

_There is also [Jupyter notebook](../llm_pipeline_experiments.ipynb) that can be experimented with._

Loads and vectorizes [DAN](https://dan.org/) content using DLT pipeleine and LanceDB. Then it evalueates RAG prompts using relevance scores from hybrid search and faithfulness using LLM as a judge. 

For this pipeline to work you need to provide the following environment variable in the .env file:

```plaintext
OPENAI_API_KEY=your_openai_api_key

LLM_MODEL=gpt-3.5-turbo #or other model
```

Pipeline supports incremental loading of the data, so you can run it multiple times and it will only load the new data. But because DAN content is vast - average first run can take up to 30 minutes.

After a successful finish you should see metrics and prompts printed out in the logs 

<img width="1283" alt="Screenshot 2024-09-14 at 12 01 41 PM" src="https://github.com/user-attachments/assets/54ffcdd7-8f9d-4843-a212-9829ea9b92c4">


as well as in the mlflow UI.

<img width="1505" alt="Screenshot 2024-09-14 at 12 03 46 PM" src="https://github.com/user-attachments/assets/37e3924a-27af-4238-a89e-12b865d1dad3">


### Training pipeline

Trains the model on the dives data using HyperOpt to pick the best parameters by looking at ROC_AUC and accuracy. After MAX_EVALUATIONS of attempts to optimize hyperparameters, it saves the best model to the MLflow registry.

### Report generation pipeline

Uses registered [Global data products](https://docs.mage.ai/orchestration/global-data-products/overview) to load other pipelines results:
- dataframes with dives data and features
- model from the MLflow registry
- table with vectorized DAN content

Predicts the rating for the dives, generates reports on each dive, augments reports with insights using vectorized DAN content, and sends final reports to Notion.

Does **require Notion token and database** id to work. If they are not provided - the pipeline will only print the data to the console.

## Optional setup steps
### How to load data from Google Drive

- Step 1: [Create a Project in the Google Cloud Console](https://developers.google.com/workspace/guides/create-project)

- Step 2: Enable the [Google Drive API](https://console.cloud.google.com/marketplace/product/google/drive.googleapis.com) for the project. 

- Step 3: Create Service Account Credentials
    - Navigate to APIs & Services > Credentials.
    - Click Create credentials and select Service account.
    - Fill in the required fields and click Create(no additional permissions required)

- Step 4: Download the Service Account Key in JSON format
    - After creating the service account, navigate to it under APIs & Services > Credentials.
    - Click on your service account.
    - Under the Keys section, click Add Key > Create New Key.
    - Choose JSON and click Create. This will download the key file to your computer.
    - Rename the file to credentials.json and place it in the root directory of the project.
- Step 5: Get the Folder ID
    - Open the folder in Google Drive. The URL will look something like this: https://drive.google.com/drive/u/0/folders/XXXXXXXXXXXXX.
    - Copy the folder ID (XXXXXXXXXXXXX).

In the .env.sample look for the section with Google Drive comment and amend the following code:

```plaintext
CREDENTIALS_PATH=/home/src/credentials.json
FOLDER_ID=XXXXXXXXXXXXX
```
Now you can run the pipeline and it will load the data from the specified folder in Google Drive.


### How to setup Notion database

- Step 1: Setup a page with properties like on the image

<img width="996" alt="Properties" src="https://github.com/user-attachments/assets/a8ec9827-53bd-41ea-829a-1ee2aeb05157">

- Step 2: Proceed with the [official tutorial](https://developers.notion.com/docs/create-a-notion-integration#create-your-integration-in-notion)
