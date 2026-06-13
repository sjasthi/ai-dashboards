from data.data_loader import DataLoader
import data.data_info as info
import data.csv_prompt_builder as prompt

# TODO: Figure out how to best import multiple files
filenames = ["students.csv", "grades.csv", "courses.csv"]

# Loads each file from list
loader = DataLoader()
for file in filenames:
    loader.add_file(file)


# Dataloader Tuple Example: file_name, dataframe

print(f"\nFiles Uploaded \n ------")
for filename, df in loader.files:
    print(f"{filename}")

print(f"\nColumns \n ------")
for filename, df in loader.files:
    print(f"{info.get_columns(df)}")


prompt = prompt.build_prompt(loader, report_goal="<TBD>")
print(prompt)
