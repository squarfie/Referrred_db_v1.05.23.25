from .models import *
from django import forms
from django.db.models import Max, Q
from apps.home.permissions import ROLE_ADMIN, ROLE_CHECKER, ROLE_ENCODER, ROLE_LAB_ENCODER, ROLE_LAB_MANAGER, ROLE_SECRETARIAT, ROLE_VERIFIER
import re


STAFF_ROLE_CHOICES = (
    (ROLE_ENCODER, ROLE_ENCODER),
    (ROLE_LAB_ENCODER, ROLE_LAB_ENCODER),
    (ROLE_SECRETARIAT, ROLE_SECRETARIAT),
    (ROLE_VERIFIER, ROLE_VERIFIER),
    (ROLE_CHECKER, ROLE_CHECKER),
    (ROLE_LAB_MANAGER, ROLE_LAB_MANAGER),
    (ROLE_ADMIN, ROLE_ADMIN),
)


def staff_role_q(*roles):
    query = Q()
    for role in roles:
        query |= Q(Staff_Role=role)
        query |= Q(Staff_Role__startswith=f"{role}|")
        query |= Q(Staff_Role__endswith=f"|{role}")
        query |= Q(Staff_Role__contains=f"|{role}|")
    return query


def staff_with_role(*roles):
    return (
        arsStaff_Details.objects
        .filter(staff_role_q(*roles))
        .select_related("User_Account")
        .distinct()
        .order_by("Staff_Name", "User_Account__first_name", "User_Account__username")
    )


def default_signature_staff(default_field):
    return arsStaff_Details.objects.filter(**{default_field: True}).order_by("Staff_Name").first()


def org_code_label(obj):
    return str(getattr(obj, "Whonet_Org_Code", "") or "").lower()


def use_lowercase_org_code_widget(field):
    css_classes = field.widget.attrs.get("class", "")
    if "text-lowercase" not in css_classes.split():
        field.widget.attrs["class"] = f"{css_classes} text-lowercase".strip()
    style = field.widget.attrs.get("style", "")
    if "text-transform" not in style:
        separator = "" if not style or style.strip().endswith(";") else "; "
        field.widget.attrs["style"] = f"{style}{separator}text-transform: lowercase;"


def resolve_organism_choice(value, organism_name=""):
    candidates = [
        str(value or "").strip(),
        str(organism_name or "").strip(),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        organism = (
            Organism_List.objects
            .filter(
                Q(Whonet_Org_Code__iexact=candidate)
                | Q(Replaced_by__iexact=candidate)
            )
            .order_by("Whonet_Org_Code")
            .first()
        )
        if organism:
            return organism

    for candidate in candidates:
        if not candidate:
            continue
        organism = (
            Organism_List.objects
            .filter(Organism__iexact=candidate)
            .order_by("Whonet_Org_Code")
            .first()
        )
        if organism:
            return organism

    return None


def batch_ref_no_from_code(batch_code):
    batch_code = (batch_code or "").strip()
    if "_" not in batch_code:
        return ""
    return batch_code.rsplit("_", 1)[-1].strip()


def apply_required_widget_attrs(form):
    for field in form.fields.values():
        widget = field.widget
        if (
            field.required
            and not widget.attrs.get("readonly")
            and not widget.attrs.get("disabled")
        ):
            widget.attrs.setdefault("required", "required")
            widget.attrs.setdefault("data-required", "true")


def duplicate_exists(model, field_name, value, instance=None):
    if value in (None, ""):
        return False
    qs = model.objects.filter(**{f"{field_name}__iexact": str(value).strip()})
    if instance and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    return qs.exists()


def recommendation_code_choices():
    codes = (
        Recommendation_items.objects
        .exclude(RecoCode__isnull=True)
        .exclude(RecoCode="")
        .order_by("RecoCode")
        .values_list("RecoCode", flat=True)
        .distinct()
    )
    return [("", "Select no"), *[(code, code) for code in codes]]


def accession_year_prefix(accession):
    match = re.match(r"\s*(\d{2})ARS", str(accession or "").strip(), re.IGNORECASE)
    return match.group(1) if match else ""


class RequiredAttrsModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_required_widget_attrs(self)



# Referred Data Upload Form
class ReferredUploadForm(forms.ModelForm):
     class Meta:
          model = ReferredData_upload
          fields = ['ReferredDataFile']


    
class Referred_Form(forms.ModelForm):

        Spec_Type = forms.ModelChoiceField(
            queryset=SpecimenTypeModel.objects.all(),
            widget=forms.Select(attrs={'class': "form-select fw-bold"}),
            empty_label="Select Specimen",
            required=False,
            error_messages={
                "invalid_choice": "Invalid specimen type. Select a specimen from the list.",
            },
        )

        ars_OrgCode = forms.ModelChoiceField(
            queryset=Organism_List.objects.all().order_by('Whonet_Org_Code'),
            to_field_name='Whonet_Org_Code',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Organism",
            required=False,
            
        )
        Site_Org = forms.ModelChoiceField(
            queryset=Organism_List.objects.all().order_by('Whonet_Org_Code'),
            to_field_name='Whonet_Org_Code', 
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Organism",
            required=False,
            
        )

        Site_OrgName = forms.CharField(
            required=False,
            widget=forms.TextInput(attrs={
                "class": "form-control fw-bold",
                "readonly": True
            })
        )

        ars_OrgName = forms.CharField(
            required=False,
            widget=forms.TextInput(attrs={
                "class": "form-control fw-bold",
                "readonly": True
            })
        )

        
        ars_reco_Code = forms.ChoiceField(
            choices=recommendation_code_choices,
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            required=False,
            
        )

        Site_Pre = forms.ModelChoiceField(
            queryset=Phenotype_Pre.objects.all().order_by('Pre_Phenotypes'),
            to_field_name='Pre_Phenotypes',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Phenotype",
            required=False,
            
        )

        Site_Pos = forms.ModelChoiceField(
            queryset=Phenotype_Post.objects.all().order_by('Post_Phenotypes'),
            to_field_name='Post_Phenotypes',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Phenotype",
            required=False,
            
        )


        ars_Pre = forms.ModelChoiceField(
            queryset=Phenotype_Pre.objects.all().order_by('Pre_Phenotypes'),
            to_field_name='Pre_Phenotypes',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Phenotype",
            required=False,
            
        )

        ars_Post = forms.ModelChoiceField(
            queryset=Phenotype_Post.objects.all().order_by('Post_Phenotypes'),
            to_field_name='Post_Phenotypes',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Phenotype",
            required=False,
            
        )
        class Meta:
            model = Referred_Data
            fields ='__all__'
            widgets = {
            'Referral_Date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'Date_Birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'Date_Admis' :forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'Spec_Date' :forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'Date_Accomplished_ARSP' :forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'RefNo' :forms.DateInput(attrs={'class': 'form-control', 'placeholder': 'ex. 0001'}),
            'BatchNo' :forms.DateInput(attrs={'class': 'form-control', 'placeholder': 'ex. 1.1'}),
            'Growth_others' :forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. after 24 hrs of incubation'}),
            'Site_Pre_ed': forms.TextInput(attrs={'class': 'form-control'}),
            'Site_Pos_ed': forms.TextInput(attrs={'class': 'form-control'}),
            'Comments': forms.Textarea(attrs={'class': 'textarea form-control', 'rows': '3'}),
            'ars_Pre_ed': forms.TextInput(attrs={'class': 'form-control'}),
            'ars_Post_ed': forms.TextInput(attrs={'class': 'form-control'}),
            'ars_reco': forms.Textarea(attrs={'class': 'textarea form-control', 'rows': '11'}),
            'ars_description': forms.Textarea(attrs={'class': 'textarea form-control', 'rows': '6'}),
            "Batch_id": forms.HiddenInput(),
            
            }

                

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['bat_seq'].widget.attrs['readonly'] = True
            accession = (
                self.data.get("AccessionNo")
                if self.is_bound
                else getattr(self.instance, "AccessionNo", "")
            )
            derived_seq = accession_ref_sequence(accession)
            if derived_seq is not None:
                self.initial["bat_seq"] = derived_seq
                self.fields["bat_seq"].initial = derived_seq
                if self.instance:
                    self.instance.bat_seq = derived_seq
            self.fields["Batch_id"].disabled = True      
            self.fields['SiteCode'].widget.attrs['readonly'] = True
            self.fields['Batch_Code'].widget.attrs['readonly'] = True
            self.fields['AccessionNo'].widget.attrs['readonly'] = True
            self.fields['Status'].required=False
            self.fields['Batch_id'].required=False
            self.fields['RefNo'].widget.attrs['readonly'] = True
            self.fields['Referral_Date'].widget.attrs['readonly'] = True
            self.fields['BatchNo'].widget.attrs['readonly'] = True
            self.fields['Site_Name'].widget.attrs['readonly'] = True
            self.fields['Age'].widget.attrs['readonly'] = True
            self.fields['Age_Display'].widget.attrs['readonly'] = True
            self.fields['Age_Display'].widget.attrs['class'] = 'form-control'
            self.fields['Age_Display'].widget.attrs['tabindex'] = '-1'
            # Dynamic queryset loading
            self.fields['Site_Org'].queryset = Organism_List.objects.all() # Always load the latest Site Code
            self.fields['Site_Org'].label_from_instance = org_code_label # Specify the field to display
            use_lowercase_org_code_widget(self.fields['Site_Org'])
            self.fields['Site_OrgName'].label_from_instance = lambda obj: obj.Organism 
            self.fields['ars_OrgCode'].queryset = Organism_List.objects.all() # Always load the latest Organism_List
            self.fields['ars_OrgCode'].label_from_instance = org_code_label # Specify the field to display
            use_lowercase_org_code_widget(self.fields['ars_OrgCode'])
            self.fields['ars_OrgName'].label_from_instance = lambda obj: obj.Organism 
            if not self.is_bound and getattr(self.instance, "pk", None):
                site_org = (getattr(self.instance, "Site_Org", "") or "").strip()
                ars_org = (getattr(self.instance, "ars_OrgCode", "") or "").strip()
                site_choice = resolve_organism_choice(
                    site_org,
                    getattr(self.instance, "Site_OrgName", ""),
                )
                ars_choice = resolve_organism_choice(
                    ars_org,
                    getattr(self.instance, "ars_OrgName", ""),
                )
                if site_choice:
                    self.initial["Site_Org"] = site_choice.Whonet_Org_Code
                    self.fields["Site_Org"].initial = site_choice.Whonet_Org_Code
                    self.initial["Site_OrgName"] = site_choice.Organism
                elif site_org:
                    self.initial["Site_Org"] = site_org
                    self.fields["Site_Org"].initial = site_org
                if ars_choice:
                    self.initial["ars_OrgCode"] = ars_choice.Whonet_Org_Code
                    self.fields["ars_OrgCode"].initial = ars_choice.Whonet_Org_Code
                    self.initial["ars_OrgName"] = ars_choice.Organism
                elif ars_org:
                    self.initial["ars_OrgCode"] = ars_org
                    self.fields["ars_OrgCode"].initial = ars_org

        def clean_bat_seq(self):
            accession = (
                self.cleaned_data.get("AccessionNo")
                or getattr(self.instance, "AccessionNo", "")
            )
            derived_seq = accession_ref_sequence(accession)
            if derived_seq is not None:
                return derived_seq
            return self.cleaned_data.get("bat_seq")

        def clean(self):
            cleaned_data = super().clean()
            accession = (
                cleaned_data.get("AccessionNo")
                or getattr(self.instance, "AccessionNo", "")
            )
            derived_seq = accession_ref_sequence(accession)
            if derived_seq is not None:
                cleaned_data["bat_seq"] = derived_seq

            return cleaned_data


        
        

#for batch table
class BatchTable_form(forms.ModelForm):
        bat_SiteCode = forms.ModelChoiceField(
            queryset=SiteData.objects.all(),
            to_field_name='SiteCode',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Site Code",
            required=False
            
        )


        bat_Checker = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

        bat_Verifier = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

        bat_LabManager = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

        bat_Encoder= forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

        bat_Head= forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

        class Meta:
            model = Batch_Table
            fields = '__all__'
            widgets = {
            'bat_Referral_Date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'bat_RefNo' :forms.DateInput(attrs={'class': 'form-control', 'placeholder': 'ex. 0001-0002'}),
            'bat_BatchNo' :forms.DateInput(attrs={'class': 'form-control', 'placeholder': 'ex. 1'}),
            'bat_Total_batch' :forms.DateInput(attrs={'class': 'form-control', 'placeholder': 'ex. 1'}),
            'bat_Date_Accomplished': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            
            # Add more fields as needed
            }

        def __init__(self, *args, **kwargs):
            super(BatchTable_form, self).__init__(*args, **kwargs)
            self.fields['bat_Encoder'].queryset = staff_with_role(ROLE_ENCODER, ROLE_ADMIN)
            self.fields['bat_Checker'].queryset = staff_with_role(ROLE_CHECKER)
            self.fields['bat_Verifier'].queryset = staff_with_role(ROLE_VERIFIER, ROLE_ADMIN)
            self.fields['bat_LabManager'].queryset = staff_with_role(ROLE_LAB_MANAGER)
            self.fields['bat_Head'].queryset = staff_with_role(ROLE_LAB_MANAGER)
            self.fields['bat_SiteCode'].queryset = SiteData.objects.all() # Always load the latest Site Code instances
            self.fields['bat_AccessionNo'].widget.attrs['readonly'] = True  # AccessionNo read-only
            self.fields['bat_Batch_Name'].widget.attrs['readonly'] = True  # Batch_Name read-only
            self.fields['bat_AccessionNoGen'].widget = forms.HiddenInput()
            self.fields['bat_Enc_Lic'].widget.attrs['readonly'] = True  
            self.fields['bat_Chec_Lic'].widget.attrs['readonly'] = True  
            self.fields['bat_Ver_Lic'].widget.attrs['readonly'] = True  
            self.fields['bat_Lab_Lic'].widget.attrs['readonly'] = True  
            self.fields['bat_Head_Lic'].widget.attrs['readonly'] = True
            self.fields['bat_Status'].required=False

            if not self.is_bound and not getattr(self.instance, "pk", None):
                default_lab_manager = default_signature_staff("Is_Default_Lab_Manager")
                if default_lab_manager:
                    self.initial["bat_LabManager"] = default_lab_manager.Staff_Name
                    self.initial["bat_Lab_Lic"] = default_lab_manager.Staff_License or ""
                default_head = default_signature_staff("Is_Default_Head")
                if default_head:
                    self.initial["bat_Head"] = default_head.Staff_Name
                    self.initial["bat_Head_Lic"] = default_head.Staff_License or ""

            # self.fields['Batch_Code'].widget = forms.HiddenInput()


         # --- Custom cleaning methods to save Staff_Name as string ---
        def clean_bat_Encoder(self):
            encoder = self.cleaned_data.get("bat_Encoder")
            return encoder.Staff_Name if encoder else ""

        def clean_bat_Checker(self):
            checker = self.cleaned_data.get("bat_Checker")
            return checker.Staff_Name if checker else ""

        def clean_bat_Verifier(self):
            verifier = self.cleaned_data.get("bat_Verifier")
            return verifier.Staff_Name if verifier else ""

        def clean_bat_LabManager(self):
            manager = self.cleaned_data.get("bat_LabManager")
            return manager.Staff_Name if manager else ""

        def clean_bat_Head(self):
            head = self.cleaned_data.get("bat_Head")
            return head.Staff_Name if head else ""


class BatchEditForm(forms.ModelForm):
    bat_SiteCode = forms.ModelChoiceField(
            queryset=SiteData.objects.all(),
            to_field_name='SiteCode',
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Site Code",
            required=False,
        )

    bat_Checker = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

    bat_Verifier = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

    bat_LabManager = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

    bat_Encoder= forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

    bat_Head= forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            to_field_name='Staff_Name',  # Specify the field you want as the value
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )
    class Meta:
        model = Batch_Table
        fields = [
            "bat_SiteCode",
            "bat_Referral_Date",
            "bat_BatchNo",
            "bat_Total_batch",
            "bat_RefNo",
            "bat_Site_NameGen",
            "bat_Batch_Name",
            "bat_Encoder",
            "bat_Enc_Lic",
            "bat_Checker",
            "bat_Chec_Lic",
            "bat_Verifier",
            "bat_Ver_Lic",
            "bat_LabManager",
            "bat_Lab_Lic",
            "bat_Head",
            "bat_Head_Lic",
            "bat_Date_Accomplished",
        ]
        widgets = {
            'bat_Referral_Date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
            'bat_RefNo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. 0001-0002'}),
            'bat_BatchNo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. 1'}),
            'bat_Total_batch': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. 1'}),
            'bat_Site_NameGen': forms.TextInput(attrs={'class': 'form-control'}),
            'bat_Batch_Name': forms.TextInput(attrs={'class': 'form-control'}),
            'bat_Date_Accomplished': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'MM/DD/YYYY'}),
        }

    def __init__(self, *args, **kwargs):
                lock_workflow_signatories = kwargs.pop("lock_workflow_signatories", False)
                super(BatchEditForm, self).__init__(*args, **kwargs)
                self.fields['bat_SiteCode'].queryset = SiteData.objects.all()
                self.fields['bat_Encoder'].queryset = staff_with_role(ROLE_ENCODER, ROLE_ADMIN)
                self.fields['bat_Checker'].queryset = staff_with_role(ROLE_CHECKER)
                self.fields['bat_Verifier'].queryset = staff_with_role(ROLE_VERIFIER, ROLE_ADMIN)
                self.fields['bat_LabManager'].queryset = staff_with_role(ROLE_LAB_MANAGER)
                self.fields['bat_Head'].queryset = staff_with_role(ROLE_LAB_MANAGER)
                if self.instance and self.instance.pk and not self.initial.get("bat_Site_NameGen"):
                    self.initial["bat_Site_NameGen"] = self.instance.bat_Site_Name
                if self.instance and self.instance.pk and not self.initial.get("bat_RefNo"):
                    self.initial["bat_RefNo"] = (
                        (self.instance.bat_RefNo or "").strip()
                        or batch_ref_no_from_code(self.instance.bat_Batch_Code)
                    )
                self.fields['bat_Batch_Name'].widget.attrs['readonly'] = True
                self.fields['bat_Enc_Lic'].widget.attrs['readonly'] = True  
                self.fields['bat_Chec_Lic'].widget.attrs['readonly'] = True  
                self.fields['bat_Ver_Lic'].widget.attrs['readonly'] = True  
                self.fields['bat_Lab_Lic'].widget.attrs['readonly'] = True  
                self.fields['bat_Head_Lic'].widget.attrs['readonly'] = True
                if not self.is_bound:
                    default_lab_manager = default_signature_staff("Is_Default_Lab_Manager")
                    if default_lab_manager and not (getattr(self.instance, "bat_LabManager", "") or "").strip():
                        self.initial["bat_LabManager"] = default_lab_manager.Staff_Name
                        self.initial["bat_Lab_Lic"] = default_lab_manager.Staff_License or ""
                    default_head = default_signature_staff("Is_Default_Head")
                    if default_head and not (getattr(self.instance, "bat_Head", "") or "").strip():
                        self.initial["bat_Head"] = default_head.Staff_Name
                        self.initial["bat_Head_Lic"] = default_head.Staff_License or ""

                if lock_workflow_signatories:
                    for field_name in (
                        "bat_Verifier",
                        "bat_Ver_Lic",
                        "bat_LabManager",
                        "bat_Lab_Lic",
                        "bat_Head",
                        "bat_Head_Lic",
                    ):
                        self.fields[field_name].disabled = True
                        self.fields[field_name].widget.attrs["title"] = (
                            "This signatory is controlled by the verification workflow."
                        )


# --- Custom cleaning methods to save Staff_Name as string ---
    def clean_bat_Encoder(self):
            encoder = self.cleaned_data.get("bat_Encoder")
            return encoder.Staff_Name if encoder else ""

    def clean_bat_Checker(self):
            checker = self.cleaned_data.get("bat_Checker")
            return checker.Staff_Name if checker else ""

    def clean_bat_Verifier(self):
            verifier = self.cleaned_data.get("bat_Verifier")
            return verifier.Staff_Name if verifier else ""

    def clean_bat_LabManager(self):
            manager = self.cleaned_data.get("bat_LabManager")
            return manager.Staff_Name if manager else ""

    def clean_bat_Head(self):
            head = self.cleaned_data.get("bat_Head")
            return head.Staff_Name if head else ""


#for adding of site code
class SiteCode_Form(RequiredAttrsModelForm):
    Site_Lab_Head_Contact = forms.CharField(required=False)
    Site_Med_Ctr_Chief_Contact = forms.CharField(required=False)
    Site_MedTech_Contact = forms.CharField(required=False)

    class Meta:
        model = SiteData
        fields = [
            'SiteCode',
            'SiteName',
            'Site_Address',
            'Site_Lab_Head',
            'Site_Lab_Head_Credentials',
            'Site_Lab_Head_Designation',
            'Site_Lab_Head_Email',
            'Site_Lab_Head_Contact',
            'Site_Med_Ctr_Chief',
            'Site_Med_Ctr_Chief_Credentials',
            'Site_Med_Ctr_Chief_Designation',
            'Site_Med_Ctr_Chief_Email',
            'Site_Med_Ctr_Chief_Contact',
            'Site_MedTech',
            'Site_MedTech_Credentials',
            'Site_MedTech_Designation',
            'Site_MedTech_Email',
            'Site_MedTech_Contact',
        ]
        widgets = {
            'Site_Address': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_SiteCode(self):
        value = (self.cleaned_data.get("SiteCode") or "").strip().upper()
        if duplicate_exists(SiteData, "SiteCode", value, self.instance):
            raise forms.ValidationError("Site code already exists.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        for name_field, credential_field in (
            ("Site_Lab_Head", "Site_Lab_Head_Credentials"),
            ("Site_Med_Ctr_Chief", "Site_Med_Ctr_Chief_Credentials"),
            ("Site_MedTech", "Site_MedTech_Credentials"),
        ):
            name = (cleaned_data.get(name_field) or "").strip()
            credentials = (cleaned_data.get(credential_field) or "").strip()
            if name and not credentials and "," in name:
                clean_name, clean_credentials = name.split(",", 1)
                cleaned_data[name_field] = clean_name.strip()
                cleaned_data[credential_field] = clean_credentials.strip()
        return cleaned_data


class SiteCode_uploadForm(RequiredAttrsModelForm):
     class Meta:
          model = SiteCode_upload
          fields = ['File_uploadSite']

#to handle many to many relationship saving
def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            self.save_m2m()
        return instance



#Breakpoints data
class BreakpointsForm(RequiredAttrsModelForm):
    EMERGING_INTERPRETATION_CHOICES = [
        ("R", "R - Resistant"),
        ("I", "I - Intermediate"),
        ("S", "S - Susceptible"),
        ("NS", "NS - Nonsusceptible"),
        ("SDD", "SDD - Susceptible dose-dependent"),
    ]

    Org = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={'class': "form-select fw-bold", "required": "required"}),
        required=True,
    )

    Whonet_Abx = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={'class': "form-select fw-bold", "required": "required"}),
        required=True,
    )

    Antibiotic = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "readonly": True, "style": "background-color: #e9ecef !important; cursor: not-allowed;"})
    )

    Abx_code = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "readonly": True, "style": "background-color: #e9ecef !important; cursor: not-allowed;"})
    )

    Tier = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "readonly": True, "style": "background-color: #e9ecef !important; cursor: not-allowed;"})
    )

    Spec_code = forms.MultipleChoiceField(
        choices=[],
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select fw-bold specimen-group-select",
                "size": 8,
                "data-skip-auto-clear": "true",
            }
        ),
        required=False,
    )

    Emerging_Pheno_Flag = forms.MultipleChoiceField(
        choices=EMERGING_INTERPRETATION_CHOICES,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select fw-bold",
                "size": 5,
                "data-skip-auto-clear": "true",
            }
        ),
        required=False,
    )

    Emerging_Pheno_Flag_Other = forms.MultipleChoiceField(
        choices=EMERGING_INTERPRETATION_CHOICES,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select fw-bold",
                "size": 5,
                "data-skip-auto-clear": "true",
            }
        ),
        required=False,
    )

    @classmethod
    def _normal_interpretation_code(cls, value):
        text = str(value or "").strip().upper()
        if not text:
            return ""
        text = text.split(" - ", 1)[0].strip()
        aliases = {
            "RESISTANT": "R",
            "INTERMEDIATE": "I",
            "SUSCEPTIBLE": "S",
            "NONSUSCEPTIBLE": "NS",
            "NON-SUSCEPTIBLE": "NS",
            "SUSCEPTIBLE DOSE-DEPENDENT": "SDD",
        }
        return aliases.get(text, text)


    class Meta:
        model = BreakpointsTable
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        org_choices = [("", "Select Organism")]
        org_choices.extend(
            (code, f"{code} - {name}" if name else code)
            for code, name in (
                Organism_List.objects
                .exclude(Whonet_Org_Code__isnull=True)
                .exclude(Whonet_Org_Code__exact="")
                .order_by("Whonet_Org_Code")
                .values_list("Whonet_Org_Code", "Organism")
            )
        )
        current_org = (getattr(self.instance, "Org", "") or "").strip()
        if current_org and current_org not in {value for value, _ in org_choices}:
            org_choices.append((current_org, current_org))
        self.fields["Org"].choices = org_choices

        whonet_choices = [("", "Select WHONET Code")]
        whonet_choices.extend(
            (code, f"{code} - {name}" if name else code)
            for code, name in (
                Antibiotic_List.objects
                .exclude(Whonet_Abx__isnull=True)
                .exclude(Whonet_Abx__exact="")
                .order_by("Whonet_Abx")
                .values_list("Whonet_Abx", "Antibiotic")
            )
        )
        current_whonet = (getattr(self.instance, "Whonet_Abx", "") or "").strip()
        if current_whonet and current_whonet not in {value for value, _ in whonet_choices}:
            whonet_choices.append((current_whonet, current_whonet))
        self.fields["Whonet_Abx"].choices = whonet_choices

        group_ids = (
            SpecimenTypeModel.objects
            .exclude(Specimen_Code_Grp__isnull=True)
            .values_list("Specimen_Code_Grp_id", flat=True)
            .distinct()
        )
        groups = (
            SpecimenTypeModel.objects
            .filter(pk__in=group_ids)
            .order_by("Specimen_Grp_Name", "Specimen_name", "Specimen_code")
        )

        choices = []
        for group in groups:
            code = (group.Specimen_code or "").strip()
            if not code:
                continue
            name = (
                group.Specimen_Grp_Name
                or group.Specimen_name
                or code
            ).strip()
            choices.append((code, f"{code} - {name}"))

        self.fields["Spec_code"].choices = choices

        if self.instance and self.instance.pk and self.instance.Spec_code:
            selected_codes = [
                code.strip().lower()
                for code in self.instance.Spec_code.split("|")
                if code.strip()
            ]
            valid_codes = {value for value, _ in choices}
            self.initial["Spec_code"] = [
                code
                for code in selected_codes
                if code in valid_codes
            ]

        for field_name in (
            "Emerging_Pheno_Flag",
            "Emerging_Pheno_Flag_Other",
        ):
            expression = getattr(self.instance, field_name, "") or ""
            selected_values = [
                self._normal_interpretation_code(value)
                for value in expression.split("|")
                if self._normal_interpretation_code(value)
            ]
            self.initial[field_name] = selected_values
            posted_values = self.data.getlist(field_name) if self.is_bound else []
            valid_values = {value for value, _ in self.fields[field_name].choices}
            for value in selected_values + [
                self._normal_interpretation_code(value)
                for value in posted_values
            ]:
                if value and value not in valid_values:
                    self.fields[field_name].choices = [
                        *self.fields[field_name].choices,
                        (value, value),
                    ]
                    valid_values.add(value)

    def clean_Spec_code(self):
        selected_codes = self.cleaned_data.get("Spec_code") or []
        return "|".join(dict.fromkeys(
            code.strip().lower()
            for code in selected_codes
            if code.strip()
        ))

    @staticmethod
    def _normal_text(value, *, upper=False):
        if value in (None, ""):
            return ""
        for attr in ("Whonet_Abx", "Whonet_Org_Code"):
            attr_value = getattr(value, attr, None)
            if attr_value not in (None, ""):
                value = attr_value
                break
        text = str(value).strip()
        return text.upper() if upper else text

    @classmethod
    def _normal_breakpoint_value(cls, value):
        text = cls._normal_text(value)
        if not text:
            return ""

        import re
        from decimal import Decimal, InvalidOperation

        match = re.match(r"^(<=|>=|<|>|=)?\s*(-?\d+(?:\.\d+)?)$", text)
        if not match:
            return text

        operator, number_text = match.groups()
        try:
            number = Decimal(number_text).normalize()
        except InvalidOperation:
            return text

        return f"{operator or ''}{format(number, 'f')}"

    def clean(self):
        cleaned_data = super().clean()

        whonet_abx = self._normal_text(cleaned_data.get("Whonet_Abx"), upper=True)
        year = self._normal_text(cleaned_data.get("Year"))
        org = self._normal_text(cleaned_data.get("Org"))
        test_method = self._normal_text(cleaned_data.get("Test_Method"), upper=True)
        spec_code = self._normal_text(cleaned_data.get("Spec_code"))
        disk_abx = bool(cleaned_data.get("Disk_Abx"))

        if test_method == "DISK":
            cleaned_data["Disk_Abx"] = True
        elif disk_abx:
            test_method = "DISK"
            cleaned_data["Test_Method"] = "DISK"

        if not (whonet_abx and year and org and test_method):
            return cleaned_data

        submitted_values = {
            "R_val": self._normal_breakpoint_value(cleaned_data.get("R_val")),
            "I_val": self._normal_breakpoint_value(cleaned_data.get("I_val")),
            "SDD_val": self._normal_breakpoint_value(cleaned_data.get("SDD_val")),
            "S_val": self._normal_breakpoint_value(cleaned_data.get("S_val")),
        }

        candidates = BreakpointsTable.objects.filter(
            Whonet_Abx=whonet_abx,
            Year=year,
            Org=org,
            Test_Method=test_method,
            Spec_code=spec_code,
        )
        if self.instance and self.instance.pk:
            candidates = candidates.exclude(pk=self.instance.pk)

        for breakpoint in candidates:
            existing_values = {
                "R_val": self._normal_breakpoint_value(breakpoint.R_val),
                "I_val": self._normal_breakpoint_value(breakpoint.I_val),
                "SDD_val": self._normal_breakpoint_value(breakpoint.SDD_val),
                "S_val": self._normal_breakpoint_value(breakpoint.S_val),
            }
            if existing_values == submitted_values:
                raise forms.ValidationError(
                    "Duplicate breakpoint already exists for this antibiotic, year, organism, test method, specimen, and breakpoint values."
                )

        return cleaned_data

    def _get_validation_exclusions(self):
        exclusions = super()._get_validation_exclusions()
        exclusions.update({
            "Emerging_Pheno_Flag",
            "Emerging_Pheno_Flag_Other",
        })
        return exclusions


    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.Whonet_Abx = self._normal_text(instance.Whonet_Abx, upper=True)
        instance.Org = self._normal_text(instance.Org)
        instance.Test_Method = self._normal_text(instance.Test_Method, upper=True)
        if instance.Test_Method == "DISK" or instance.Disk_Abx:
            instance.Test_Method = "DISK"
            instance.Disk_Abx = True

        for field_name in (
            "Emerging_Pheno_Flag",
            "Emerging_Pheno_Flag_Other",
        ):
            values = self.cleaned_data.get(field_name) or []
            setattr(
                instance,
                field_name,
                "|".join(dict.fromkeys(
                    self._normal_interpretation_code(value)
                    for value in values
                    if self._normal_interpretation_code(value)
                )),
            )

        if instance.Whonet_Abx:
            try:
                abx = Antibiotic_List.objects.get(
                    Whonet_Abx=instance.Whonet_Abx
                )

                instance.Antibiotic = abx.Antibiotic
                instance.Abx_code = abx.Abx_code
                instance.Tier = abx.Tier
                instance.Antibiotic_list = abx

            except Antibiotic_List.DoesNotExist:
                pass

        if commit:
            instance.save()

        return instance

                        

class Breakpoint_uploadForm(RequiredAttrsModelForm):
     class Meta:
          model = Breakpoint_upload
          fields = ['File_uploadBP']

#ensure only csv, tsv, and excel are uploaded
def clean_file_upload(self):
        file = self.cleaned_data.get('File_uploadBP') #make sure this matches the model 
        if file:
            if not file.name.lower().endswith(('.csv', '.tsv', '.xlsx', '.xls')):
                raise forms.ValidationError('File must be a CSV, TSV, or Excel file.')
        return file

#for antibiotic entry form
class AntibioticEntryForm(forms.ModelForm):
        ab_Abx_code = forms.ModelChoiceField(
            queryset=BreakpointsTable.objects.all(),
            to_field_name='Antibiotic',
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Antibiotic",
            required=False,
        )
        
        class Meta:
            model = AntibioticEntry
            fields = '__all__'

        def __init__(self, *args, **kwargs):
            super(AntibioticEntryForm, self).__init__(*args, **kwargs)
            self.fields['ab_AccessionNo'].widget.attrs['readonly'] = True  # Make Accssion read-only


class RawAntibioticUploadForm(forms.ModelForm):
    class Meta:
        model = RawAntibiotic_upload
        fields = ['RawAntibioticFile']
        widgets = {
            'RawAntibioticFile': forms.ClearableFileInput(attrs={'class': 'form-control-file'}),
        }




class SpecimenTypeForm(RequiredAttrsModelForm):
    class Meta:
        model = SpecimenTypeModel  
        fields = ['Specimen_name', 'Specimen_code', 'Emerging_Spec_Flag', 'Specimen_Code_Grp', 'Specimen_Grp_Name']  # Include the fields you want in the form
        widgets = {
             "Emerging_Spec_Flag": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            vals = SpecimenTypeModel.objects.values_list("Specimen_code", flat=True).distinct()
            self.fields["Specimen_Code_Grp"].choices = [(v, v) for v in vals]

    def clean_Specimen_code(self):
        value = (self.cleaned_data.get("Specimen_code") or "").strip().lower()
        if duplicate_exists(SpecimenTypeModel, "Specimen_code", value, self.instance):
            raise forms.ValidationError("Specimen code already exists.")
        return value


class SpecimenUploadForm(RequiredAttrsModelForm):
    class Meta:
        model = Specimen_upload
        fields = ['File_uploadSpec']
        widgets = {
            'File_uploadSpec': forms.FileInput(attrs={'class': 'form-control'})
        }



class ContactForm(RequiredAttrsModelForm):
    Staff_Role = forms.MultipleChoiceField(
        choices=STAFF_ROLE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "custom-control-input"}),
        help_text="Select one or more roles for this staff member.",
    )

    class Meta:
        model = arsStaff_Details
        fields = '__all__'
        widgets = {
            "User_Account": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        self.can_edit_roles = kwargs.pop("can_edit_roles", True)
        super(ContactForm, self).__init__(*args, **kwargs)
        roles = str(getattr(self.instance, "Staff_Role", "") or "")
        self.initial["Staff_Role"] = [
            role.strip()
            for role in roles.replace(",", "|").replace(";", "|").split("|")
            if role.strip()
        ]
        self.fields['Staff_Telnum'].widget = forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '09171234567',  # Philippine phone number format
            'readonly': False  # Ensure it's not blocking JavaScript updates
        })
        if not self.can_edit_roles:
            self.fields["Staff_Role"].required = False
            self.fields["Is_Default_Lab_Manager"].disabled = True
            self.fields["Is_Default_Head"].disabled = True

    def clean_Staff_Role(self):
        if not self.can_edit_roles:
            return getattr(self.instance, "Staff_Role", "") or ""
        roles = self.cleaned_data.get("Staff_Role") or []
        return "|".join(dict.fromkeys(roles))

    def clean_User_Account(self):
        value = self.cleaned_data.get("User_Account")
        if value and arsStaff_Details.objects.filter(User_Account=value).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This user account is already assigned to another staff record.")
        return value

    def clean_Staff_Name(self):
        value = (self.cleaned_data.get("Staff_Name") or "").strip()
        account = self.cleaned_data.get("User_Account")
        if not value and account:
            value = (account.get_full_name() or account.username or "").strip()
        if duplicate_exists(arsStaff_Details, "Staff_Name", value, self.instance):
            raise forms.ValidationError("Staff name already exists.")
        return value

    def clean_Staff_EmailAdd(self):
        value = (self.cleaned_data.get("Staff_EmailAdd") or "").strip()
        if duplicate_exists(arsStaff_Details, "Staff_EmailAdd", value, self.instance):
            raise forms.ValidationError("Staff email already exists.")
        return value

    def _get_validation_exclusions(self):
        exclusions = super()._get_validation_exclusions()
        exclusions.add("Staff_Role")
        return exclusions



#Antibiotic Data
class AntibioticsForm(RequiredAttrsModelForm):
     class Meta:
          model = Antibiotic_List
          fields = '__all__'
          widgets = {
               'Antibiotitc' :forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. Amoxicillin'}),
               'Abx_code' :forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. AMX'}),
               'Whonet_Abx':forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ex. AMX_ND10 if disk or AMX_NM'}), 
          }

     def clean_Whonet_Abx(self):
        value = (self.cleaned_data.get("Whonet_Abx") or "").strip().upper()
        if duplicate_exists(Antibiotic_List, "Whonet_Abx", value, self.instance):
            raise forms.ValidationError("Antibiotic code already exists.")
        return value
          
     def save(self, commit=True):
        instance = super().save(commit=False)
        test_method = (instance.Test_Method or "").strip().upper()
        instance.Test_Method = test_method
        instance.Disk_Abx = test_method == "DISK"
        model_field_names = {field.name for field in instance._meta.fields}

        if self.instance and self.instance.pk:
            for hidden_flag in ("Show_All", "Show_Panel", "Method_specific"):
                if hidden_flag not in model_field_names:
                    continue
                if hidden_flag not in self.data:
                    setattr(instance, hidden_flag, getattr(self.instance, hidden_flag))
        else:
            if "Show_All" in model_field_names and "Show_All" not in self.data:
                instance.Show_All = True
            if "Show_Panel" in model_field_names and "Show_Panel" not in self.data:
                instance.Show_Panel = False
            if "Method_specific" in model_field_names and "Method_specific" not in self.data:
                instance.Method_specific = False
        
        # Replace None with an empty string or another default value
        for field_name in self.fields:
            value = getattr(instance, field_name)
            if value is None:
                setattr(instance, field_name, '')

        if commit:
            instance.save()
            self.save_m2m()
        return instance

class Antibiotics_uploadForm(RequiredAttrsModelForm):
     class Meta:
          model = Antibiotic_upload
          fields = ['File_uploadAbx']


# Organism Data
class OrganismForm(RequiredAttrsModelForm):
     class Meta:
          model = Organism_List
          fields = '__all__'

     def clean_Whonet_Org_Code(self):
        value = (self.cleaned_data.get("Whonet_Org_Code") or "").strip().lower()
        if duplicate_exists(Organism_List, "Whonet_Org_Code", value, self.instance):
            raise forms.ValidationError("Organism code already exists.")
        return value

     def clean(self):
        cleaned_data = super().clean()
        for field in Organism_List.UPPERCASE_CODE_FIELDS:
            value = cleaned_data.get(field)
            if value:
                cleaned_data[field] = value.strip().upper()
        return cleaned_data


class Organism_uploadForm(RequiredAttrsModelForm):
     class Meta:
          model = Organism_upload
          fields =['File_uploadOrg']


class Emerge_Pheno_Form(RequiredAttrsModelForm):

        class Meta:
          model = Emerging_Filter_Age
          fields = '__all__'

        def clean_Eme_Age(self):
          value = self.cleaned_data.get("Eme_Age")
          if value is not None and Emerging_Filter_Age.objects.filter(Eme_Age=value).exclude(pk=self.instance.pk).exists():
               raise forms.ValidationError("Emerging age criterion already exists.")
          return value


class Eme_Crit_Upload_Form(RequiredAttrsModelForm):
     class Meta:
          model = Emerging_Crit_upload
          fields =['File_uploadEme']



class Phenotype_Pre_Form(RequiredAttrsModelForm):
     class Meta:
          model = Phenotype_Pre
          fields = '__all__'

     def clean_Pre_Phenotypes(self):
        value = (self.cleaned_data.get("Pre_Phenotypes") or "").strip()
        if duplicate_exists(Phenotype_Pre, "Pre_Phenotypes", value, self.instance):
            raise forms.ValidationError("Phenotype pre already exists.")
        return value

class Pheno_pre_upForm(RequiredAttrsModelForm):
     class Meta:
          model = Pheno_upload_Pre
          fields = ['File_Pheno_pre']



class Phenotype_Post_Form(RequiredAttrsModelForm):
     class Meta:
          model = Phenotype_Post
          fields = '__all__'

     def clean_Post_Phenotypes(self):
        value = (self.cleaned_data.get("Post_Phenotypes") or "").strip()
        if duplicate_exists(Phenotype_Post, "Post_Phenotypes", value, self.instance):
            raise forms.ValidationError("Phenotype post already exists.")
        return value

class Pheno_post_upForm(RequiredAttrsModelForm):
     class Meta:
          model = Pheno_upload_Post
          fields = ['File_Pheno_post']




class Recco_item_Form(RequiredAttrsModelForm):
     class Meta:
          model = Recommendation_items
          fields = '__all__'

     def clean_RecoCode(self):
        value = (self.cleaned_data.get("RecoCode") or "").strip()
        if duplicate_exists(Recommendation_items, "RecoCode", value, self.instance):
            raise forms.ValidationError("Recommendation code already exists.")
        return value
          


class Reco_item_upForm (RequiredAttrsModelForm):
     class Meta:
          model = Reco_item_upload
          fields = ['File_reco_desc']



#for tat monitoring
class TATStepConfigUploadForm(RequiredAttrsModelForm):
    class Meta:
        model = TATStepConfigUpload
        fields = ['tat_file']
        widgets = {
            'tat_file': forms.FileInput(attrs={'class': 'form-control'})
        }


class TATMonitoringForm(forms.ModelForm):
    class Meta:
        model = TATform
        exclude = ['tat_Date_Last_Update', 'tat_Batch_Isolates', 'tat_Status_Release', 'tat_Target_Days']
        widgets = {
            'tat_Referral_Date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'tat_Date_Released': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'tat_Running_TAT': forms.TextInput(attrs={
                'class': 'form-control bg-light', 
                'readonly': 'readonly',
                'style': 'cursor: not-allowed;'  # inject inline CSS here!
            }),
            'tat_Final_TAT': forms.TextInput(attrs={
                'class': 'form-control bg-light', 
                'readonly': 'readonly',
                'style': 'cursor: not-allowed;'  # inject inline CSS here!
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        location_names = list(
            TATLocation.objects
            .filter(is_active=True)
            .order_by("order", "name")
            .values_list("name", flat=True)
        )
        if not location_names:
            location_names = ["n/a"]
        current_location = getattr(self.instance, "tat_Batch_Location", "") or "n/a"
        if current_location and current_location not in location_names:
            location_names.append(current_location)
        self.fields["tat_Batch_Location"] = forms.ChoiceField(
            choices=[(name, name) for name in location_names],
            required=False,
            widget=forms.Select(attrs={"class": "form-control"}),
        )
        self.fields["tat_Running_TAT"].disabled = True
        self.fields["tat_Final_TAT"].disabled = True
        self.fields["tat_BatchNumber"].disabled = True
        self.fields["tat_Total_Batch"].disabled = True
        for field_name in ("tat_BatchNumber", "tat_Total_Batch"):
            self.fields[field_name].widget.attrs.update({
                "class": "form-control bg-light",
                "readonly": "readonly",
                "style": "cursor: not-allowed;",
            })

    def clean_tat_Running_TAT(self):
        return self.instance.tat_Running_TAT

    def clean_tat_Final_TAT(self):
        return self.instance.tat_Final_TAT

    def clean_tat_BatchNumber(self):
        return self.instance.tat_BatchNumber

    def clean_tat_Total_Batch(self):
        return self.instance.tat_Total_Batch


class TATOverallSettingForm(RequiredAttrsModelForm):
    class Meta:
        model = TATOverallSetting
        fields = ["target_days"]
        widgets = {
            "target_days": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
                "placeholder": "Enter overall allowed days",
            }),
        }


class TATStepConfigForm(RequiredAttrsModelForm):
    class Meta:
        model = TATStepConfig
        fields = '__all__'
        widgets = {
            'step_type': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter step name'
            }),
            'step_owner': forms.Select(attrs={
                'class': 'form-control'
            }),
            'target_days': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0
            }),
            'order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        step_type = cleaned_data.get('step_type')
        step_owner = cleaned_data.get('step_owner')

        if TATStepConfig.objects.filter(
            step_type=step_type,
            step_owner=step_owner
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                "This step configuration already exists."
            )

        return cleaned_data



class TATLocationForm(RequiredAttrsModelForm):
    class Meta:
        model = TATLocation
        fields = ["name", "order", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Enter location item",
            }),
            "order": forms.NumberInput(attrs={
                "class": "form-control",
                "min": 0,
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "form-check-input",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["order"].required = False

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Location name is required.")
        if TATLocation.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This TAT location already exists.")
        return name

    def clean_order(self):
        order = self.cleaned_data.get("order")
        if order is not None:
            return order

        last_order = (
            TATLocation.objects
            .exclude(pk=self.instance.pk)
            .aggregate(Max("order"))
            .get("order__max")
            or 0
        )
        return last_order + 1


class TATStepForm(forms.ModelForm):
    performed_by = forms.ModelChoiceField(
            queryset=arsStaff_Details.objects.all(),
            widget=forms.Select(attrs={'class': "form-select fw-bold", 'style': 'max-width: auto;'}),
            empty_label="Select Staff",
            required=False,
        )

    class Meta:
        model = TATStep
        fields = [
            'step_config',
            'date_received',
            'date_finished',
            'performed_by',
            'remarks',
        ]
        widgets = {
            'step_config': forms.Select(attrs={'class': 'form-control'}),
            'date_received': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'date_finished': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'remarks': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        }


TATStepFormSet = forms.inlineformset_factory(
    TATform, 
    TATStep, 
    form=TATStepForm,  
    extra=1, 
    can_delete=True
)



class NonWorkingDayForm(RequiredAttrsModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_recurring"].required = False

    class Meta:
        model = NonWorkingDay
        fields = ['date', 'description', 'applies_to', 'is_recurring']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'applies_to': forms.Select(attrs={'class': 'form-control'}),
            'is_recurring': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
